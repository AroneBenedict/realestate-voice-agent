"""Checks applied to every reply before it is spoken.

The rule-based agent should never trip these; they exist as the last line of defence for an
LLM-driven agent, and the evaluation counts every violation either way.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .catalog import Catalog
from .voice import lakh_phrase

MAX_WORDS = 55  # a voice reply longer than this loses the caller

PROMISES = re.compile(
    r"\b(\d+\s*(percent|%)\s*(off|discount)|guaranteed?|assured returns?|i can (give|offer) you|special discount"
    r"|price will (go up|increase)|only \d+ units? left|last unit|best deal in)\b", re.I)
ADVICE = re.compile(r"\b(you should (buy|invest)|title is clear|no legal issues|tax[- ]free|will appreciate)\b", re.I)
AMOUNT = re.compile(r"(\d+(?:\.\d+)?)\s*(lakh|crore)", re.I)
PHONE = re.compile(r"\b[6-9]\d{9}\b")


@dataclass(frozen=True)
class Violation:
    rule: str
    detail: str


class Guardrails:
    def __init__(self, catalog: Catalog):
        self.allowed_amounts = {
            lakh_phrase(value) for project in catalog.projects for config in project.configs
            for value in (config.price_min_lakh, config.price_max_lakh)
        }
        self.project_names = {project.name.lower() for project in catalog.projects}

    def check(self, reply: str, caller_budget_phrases: set[str] | None = None) -> list[Violation]:
        found: list[Violation] = []
        words = len(reply.split())
        if words > MAX_WORDS:
            found.append(Violation("too_long_for_voice", f"{words} words"))
        if match := PROMISES.search(reply):
            found.append(Violation("unauthorised_promise", match.group(0)))
        if match := ADVICE.search(reply):
            found.append(Violation("legal_or_financial_advice", match.group(0)))
        if PHONE.search(reply):
            found.append(Violation("reads_out_full_phone_number", "use the last four digits only"))
        allowed = self.allowed_amounts | (caller_budget_phrases or set())
        for number, unit in AMOUNT.findall(reply):
            phrase = f"{float(number):g} {unit.lower()}"
            if phrase not in allowed:
                found.append(Violation("price_not_in_catalogue", phrase))
        return found


SAFE_FALLBACK = "Let me have our sales expert confirm that for you. Is there anything else I can help with?"


def redact(text: str) -> str:
    """Mask phone numbers in logs and transcripts: keep the last four digits."""
    return PHONE.sub(lambda m: "XXXXXX" + m.group(0)[-4:], text)
