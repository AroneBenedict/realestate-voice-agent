"""LLM-driven variant of the agent, using Claude tool calling.

Instead of the fixed call flow, an LLM reads the persona prompt and the conversation, and
decides each turn which tools to call and what to say. The tools are the same catalogue,
brochure and CRM functions the rule-based agent uses, and every reply passes the same
guardrails before it is spoken.

Not benchmarked: the reported numbers come from the rule-based agent. This path is covered
by unit tests against a fake Messages API client. ``anthropic`` is imported lazily.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .catalog import BrochureIndex, Catalog
from .crm import CRM
from .guardrails import SAFE_FALLBACK, Guardrails
from .voice import lakh_phrase, speakable

DEFAULT_MODEL = "claude-sonnet-5-5"
PERSONA_FILE = Path(__file__).resolve().parents[2] / "prompts" / "persona.md"
MAX_TOOL_ROUNDS = 5


def _schema(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


TOOLS = [
    {"name": "search_properties",
     "description": "Find projects with the requested BHK in a locality whose price overlaps the budget (lakh rupees). "
                    "Returns at most two projects with their price range and possession date.",
     "input_schema": _schema({"locality": {"type": "string"}, "bhk": {"type": "integer"},
                              "budget_max_lakh": {"type": "number"}, "budget_min_lakh": {"type": "number"}},
                             ["locality", "bhk", "budget_max_lakh"])},
    {"name": "get_project_fact",
     "description": "Look up one fact from a project's brochure, e.g. possession, parking, pets, metro distance. "
                    "If the brochure does not say, the result says so: never guess.",
     "input_schema": _schema({"project_id": {"type": "string"}, "question": {"type": "string"}},
                             ["project_id", "question"])},
    {"name": "update_lead",
     "description": "Save what the caller has told you to the CRM.",
     "input_schema": _schema({"name": {"type": "string"}, "phone": {"type": "string"}, "locality": {"type": "string"},
                              "bhk": {"type": "integer"}, "budget_max_lakh": {"type": "number"},
                              "timeline": {"type": "string", "enum": ["0-3 months", "3-6 months", "6-12 months",
                                                                      "exploring"]}}, [])},
    {"name": "book_site_visit",
     "description": "Book a site visit. Needs the caller's phone number saved first.",
     "input_schema": _schema({"project_id": {"type": "string"}, "day": {"type": "string"}}, ["project_id", "day"])},
    {"name": "end_call",
     "description": "End the call with an outcome after saying goodbye.",
     "input_schema": _schema({"outcome": {"type": "string", "enum": ["visit_booked", "qualified", "no_match",
                                                                     "not_interested", "do_not_call", "callback",
                                                                     "handoff"]}}, ["outcome"])},
]


class LLMCall:
    def __init__(self, catalog: Catalog, crm: CRM, client: Any = None, model: str | None = None,
                 persona: str | None = None):
        if client is None:
            try:
                import anthropic
            except ImportError as exc:  # pragma: no cover - depends on the environment
                raise RuntimeError("install the 'anthropic' package to use LLMCall") from exc
            client = anthropic.Anthropic()
        self.catalog, self.crm, self.client = catalog, crm, client
        self.model = model or os.environ.get("VOICEAGENT_MODEL", DEFAULT_MODEL)
        self.system = persona or PERSONA_FILE.read_text(encoding="utf-8")
        self.guardrails = Guardrails(catalog)
        self.brochure = BrochureIndex()
        self.lead = crm.create_lead()
        self.messages: list[dict] = []
        self.trace: list[dict] = []
        self.violations: list = []
        self.ended = False

    # ------------------------------------------------------------------ tools

    def _run_tool(self, name: str, args: dict) -> tuple[dict, bool]:
        try:
            if name == "search_properties":
                matches = self.catalog.search(args["locality"], int(args["bhk"]), args.get("budget_min_lakh"),
                                              float(args["budget_max_lakh"]))
                return {"projects": [
                    {"project_id": m.project.id, "name": m.project.name, "locality": m.project.locality,
                     "price": f"{lakh_phrase(m.config.price_min_lakh)} to {lakh_phrase(m.config.price_max_lakh)}",
                     "possession": m.project.facts["possession"], "exact_locality": m.exact_locality}
                    for m in matches]}, True
            if name == "get_project_fact":
                project = self.catalog.by_id.get(args["project_id"])
                if project is None:
                    return {"error": f"unknown project_id {args['project_id']!r}"}, False
                answer = self.brochure.answer(project, args["question"])
                return {"answer": answer.text or "The brochure does not say. Offer to have the sales team confirm."}, True
            if name == "update_lead":
                self.crm.update_lead(self.lead.id, **args)
                return {"saved": sorted(args)}, True
            if name == "book_site_visit":
                if args["project_id"] not in self.catalog.by_id:
                    return {"error": f"unknown project_id {args['project_id']!r}"}, False
                self.crm.book_visit(self.lead.id, args["project_id"], args["day"])
                return {"booked": True}, True
            if name == "end_call":
                self.crm.update_lead(self.lead.id, status=args["outcome"])
                self.ended = True
                return {"ended": True}, True
            return {"error": f"unknown tool {name!r}"}, False
        except (KeyError, ValueError, TypeError) as exc:
            return {"error": str(exc).strip("'\"")}, False

    # ------------------------------------------------------------------ loop

    def respond(self, utterance: str) -> str:
        """One caller turn: let the model call tools until it produces what to say."""
        self.messages.append({"role": "user", "content": utterance})
        for _ in range(MAX_TOOL_ROUNDS):
            response = self.client.messages.create(model=self.model, max_tokens=400, system=self.system,
                                                   tools=TOOLS, messages=self.messages)
            blocks = [self._plain(b) for b in response.content]
            self.messages.append({"role": "assistant", "content": blocks})
            calls = [b for b in blocks if b["type"] == "tool_use"]
            if not calls:
                text = " ".join(b["text"] for b in blocks if b["type"] == "text").strip()
                return self._guard(text)
            results = []
            for call in calls:
                result, ok = self._run_tool(call["name"], call["input"])
                self.trace.append({"tool": call["name"], "ok": ok})
                results.append({"type": "tool_result", "tool_use_id": call["id"], "content": json.dumps(result),
                                "is_error": not ok})
            self.messages.append({"role": "user", "content": results})
            if self.ended:
                text = " ".join(b["text"] for b in blocks if b["type"] == "text").strip()
                return self._guard(text or "Thank you for your time. Goodbye!")
        return self._guard("Let me connect you with our sales expert who can help further.")

    def _guard(self, text: str) -> str:
        text = speakable(text)
        problems = self.guardrails.check(text)
        if problems:
            self.violations.extend(problems)
            self.trace.append({"tool": "guardrail.blocked", "rules": [p.rule for p in problems]})
            return SAFE_FALLBACK
        return text

    @staticmethod
    def _plain(block: Any) -> dict:
        if isinstance(block, dict):
            return block
        if block.type == "tool_use":
            return {"type": "tool_use", "id": block.id, "name": block.name, "input": dict(block.input)}
        return {"type": "text", "text": block.text}
