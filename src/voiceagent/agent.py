"""The sales agent: qualifies a real-estate lead over a phone call.

One ``Call`` object holds one conversation. ``Call.respond(utterance)`` takes what the caller
said (already transcribed) and returns what the agent says next, plus what it now expects.

Call flow:
    opening -> qualifying (locality, BHK, budget, timeline) -> recommending -> site visit -> close

At any point the caller can ask a brochure question, ask for a discount or legal advice, ask
for a human, say they are busy, say they are not interested, or ask not to be called again;
each has a fixed, safe handling. Every tool call is recorded in ``Call.trace``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .catalog import BrochureIndex, Catalog, Match
from .crm import CRM
from .guardrails import SAFE_FALLBACK, Guardrails, Violation, redact
from .nlu import Parse, parse
from .voice import lakh_phrase, speakable

PERSONA = "Asha"
COMPANY = "Sample Realty"
REQUIRED = ("locality", "bhk", "budget", "timeline")
QUESTIONS = {
    "locality": "Which area of Bengaluru are you looking at?",
    "bhk": "How many bedrooms do you need: 1, 2, 3 or 4 BHK?",
    "budget": "What budget do you have in mind?",
    "timeline": "And when are you planning to buy: in the next few months, or later?",
}
REASK = {**QUESTIONS, "phone": "Could you tell me your mobile number again?", "name": "Could you tell me your name again?",
         "visit_day": "Which day would you like to visit?"}
DEFER = "I don't have that detail in the brochure, so I'll have our sales team confirm it for you."
MAX_TURNS = 24
NOT_NAMES = {"yes", "yeah", "sure", "ok", "okay", "it", "is", "its", "it's", "my", "name", "the", "uh", "um", "so",
             "like", "actually", "basically", "call", "me", "this", "i", "am", "please", "hi", "hello", "sir", "mr", "ms"}


@dataclass(frozen=True)
class AgentConfig:
    """Switches for the robustness features, so each can be measured on its own."""

    spoken_numbers: bool = True       # "eighty five lakh" -> 85 lakh
    fuzzy_locality: bool = True       # "white feel" -> Whitefield
    confirm_uncertain: bool = True    # read back anything understood only approximately
    contextual_numbers: bool = True   # a bare "85" after "what budget?" means 85 lakh
    confirm_summary: bool = True      # read the requirements back once before searching
    chunked_phone: bool = True        # after a failed dictation, take the number in two halves


@dataclass
class Turn:
    text: str
    expecting: str | None
    ended: bool = False


@dataclass
class TraceEvent:
    turn: int
    tool: str
    detail: dict


@dataclass
class Call:
    catalog: Catalog
    crm: CRM
    config: AgentConfig = field(default_factory=AgentConfig)
    brochure: BrochureIndex = field(default_factory=BrochureIndex)

    def __post_init__(self) -> None:
        self.guardrails = Guardrails(self.catalog)
        self.slots: dict = {}
        self.pending_confirm: tuple[str, object] | None = None
        self.expecting: str | None = None
        self.stage = "opening"
        self.presented: list[Match] = []
        self.visit_project: str | None = None
        self.ended = False
        self.turns = 0
        self.transcript: list[tuple[str, str]] = []
        self.trace: list[TraceEvent] = []
        self.violations: list[Violation] = []
        self.last_text = ""
        self.caller_number: str | None = None  # known from the telephony layer on an outbound call
        self.phone_attempts = 0
        self.summary_confirmed = False
        self.last_question: tuple[str, str | None] = ("", None)
        self.lead = self.crm.create_lead()
        self._log("crm.create_lead", lead_id=self.lead.id)

    # ------------------------------------------------------------------ helpers

    def _log(self, tool: str, **detail) -> None:
        self.trace.append(TraceEvent(self.turns, tool, detail))

    def _say(self, text: str, expecting: str | None, end: bool = False) -> Turn:
        text = speakable(text)
        budgets = [self.slots.get("budget")]
        if self.pending_confirm and self.pending_confirm[0] == "budget":
            budgets.append(self.pending_confirm[1])
        caller_amounts = {lakh_phrase(v) for b in budgets if b for v in b if v is not None}
        problems = self.guardrails.check(text, caller_amounts)
        if problems:
            self.violations.extend(problems)
            self._log("guardrail.blocked", rules=[p.rule for p in problems])
            text = SAFE_FALLBACK
        self.expecting = None if end else expecting
        self.ended = self.ended or end
        if not end:
            question = text.split(". ")[-1] if ". " in text else text
            self.last_question = (question if question.endswith("?") else text, self.expecting)
        self.transcript.append(("agent", text))
        return Turn(text, self.expecting, self.ended)

    def _end(self, text: str, status: str) -> Turn:
        self.stage = "closed"
        self._save(status=status)
        return self._say(text, None, end=True)

    def _save(self, **extra) -> None:
        budget = self.slots.get("budget")
        fields = {
            "name": self.slots.get("name"), "phone": self.slots.get("phone"), "locality": self.slots.get("locality"),
            "bhk": self.slots.get("bhk"), "timeline": self.slots.get("timeline"), "purpose": self.slots.get("purpose"),
            "budget_min_lakh": budget[0] if budget else None, "budget_max_lakh": budget[1] if budget else None,
            "interested_in": [m.project.id for m in self.presented], "score": self._score(),
        }
        fields.update(extra)
        self.crm.update_lead(self.lead.id, **fields)
        self._log("crm.update_lead", **{k: v for k, v in fields.items() if k not in ("phone", "name")})

    def _score(self) -> str:
        timeline = self.slots.get("timeline")
        booked = self.lead.visit is not None
        soon = timeline in ("0-3 months", "3-6 months")
        if booked and soon:
            return "hot"
        if booked or (soon and self.presented):
            return "warm"
        return "cold"

    def _next_prompt(self, prefix: str = "") -> Turn:
        """Ask for the next missing slot, or move on to recommendations."""
        for slot in REQUIRED:
            if slot not in self.slots:
                self.stage = "qualifying"
                return self._say(f"{prefix} {QUESTIONS[slot]}".strip(), slot)
        if self.stage in ("opening", "qualifying"):
            if self.config.confirm_summary and not self.summary_confirmed:
                return self._say(f"{prefix} Let me confirm: {self._summary()}. Is that right?".strip(), "confirm_summary")
            return self._recommend(prefix)
        if self.stage == "recommending":
            return self._say(f"{prefix} Would you like to book a site visit?".strip(), "visit_consent")
        if self.stage == "visit":
            return self._visit_step(prefix)
        return self._say(f"{prefix} Is there anything else I can help with?".strip(), "anything_else")

    # ------------------------------------------------------------------ main entry

    def _summary(self) -> str:
        low, high = self.slots["budget"]
        budget = f"{lakh_phrase(low)} to {lakh_phrase(high)}" if low else f"up to {lakh_phrase(high)}"
        when = {"0-3 months": "in the next three months", "3-6 months": "in three to six months",
                "6-12 months": "within a year", "exploring": "with no fixed timeline"}[self.slots["timeline"]]
        return f"a {self.slots['bhk']} BHK in {self.slots['locality']}, budget {budget}, buying {when}"

    def opening(self) -> Turn:
        return self._say(f"Hi, this is {PERSONA} from {COMPANY}. You had enquired about a new home in Bengaluru. "
                         "Is this a good time to talk for two minutes?", "consent")

    def respond(self, utterance: str) -> Turn:
        if self.ended:
            raise RuntimeError("call has ended")
        self.turns += 1
        self.transcript.append(("caller", redact(utterance)))
        if self.turns > MAX_TURNS:
            self._log("handoff_to_human", reason="turn limit")
            return self._end("Let me connect you with our sales expert who can help further. Thank you!", "handoff")
        p = parse(utterance, spoken_numbers=self.config.spoken_numbers, fuzzy_locality=self.config.fuzzy_locality)
        self.last_text = p.slots["_text"]
        self._log("nlu.parse", intent=p.intent, slots=sorted(k for k in p.slots if not k.startswith("_")),
                  uncertain=sorted(p.uncertain))

        if not p.slots["_text"] and p.intent is None:
            text, expecting = self.last_question
            return self._say(f"Sorry, I didn't catch that. {text}", expecting)

        exit_turn = self._global_intents(p)
        if exit_turn is not None:
            return exit_turn

        if self.expecting == "confirm_summary":
            corrections = {k for k in REQUIRED if k in p.slots}
            if p.intent == "affirm" and not corrections:
                self.summary_confirmed = True
                return self._recommend("")
            if not corrections:
                return self._say("Sorry, what should I change: the area, the BHK, the budget or the timing?",
                                 "correction")
        if self.expecting == "correction" and not any(k in p.slots for k in REQUIRED):
            self._contextual(p)

        if self.pending_confirm is not None:
            turn = self._resolve_confirmation(p)
            if turn is not None:
                return turn

        if self.expecting == "consent":
            if p.intent == "deny":
                self._log("schedule_callback", day=p.slots.get("visit_day"))
                return self._end("No problem, I'll call you back at a better time. Have a nice day!", "callback")
            self.stage = "qualifying"

        prefix = self._absorb_slots(p)
        if isinstance(prefix, Turn):
            return prefix

        if (p.intent is None and self.presented and not any(k in p.slots for k in REQUIRED)
                and self.brochure.topic(p.slots["_text"])[0] is not None):
            p.intent = "question"  # the question word was lost in transcription, but the topic is clear
        if p.intent == "question" or (self.expecting == "repeat_question" and self.presented):
            return self._answer_question(p, prefix)
        if p.intent == "discount":
            return self._next_prompt(prefix + " Prices are set by the developer, so I can't offer a discount myself, "
                                     "but our sales manager can walk you through the pricing at the site visit.")
        if p.intent == "legal":
            return self._next_prompt(prefix + " I can't give legal, tax or investment advice. Our team can share the "
                                     "project documents for your lawyer to review.")
        if p.intent == "off_topic":
            return self._next_prompt(prefix + " I'm only able to help with your home search.")

        if self.stage == "recommending" and self.expecting == "visit_consent":
            if p.intent in ("affirm", "site_visit") or "visit_day" in p.slots:
                self.stage = "visit"
                return self._visit_step(prefix)
            if p.intent == "deny":
                return self._end("No problem. I'll save these options for you, and our team can share the brochures. "
                                 "Thank you for your time!", "qualified")
        if self.expecting == "phone_on_file":
            if p.intent == "affirm" and "phone" not in p.slots:
                self.slots["phone"] = self.caller_number
            elif "phone" not in p.slots:
                return self._say(f"{prefix} No problem. Which number should I use?".strip(), "phone")
        if self.stage == "visit":
            return self._visit_step(prefix)
        if self.expecting == "anything_else" and (p.intent in ("deny", "affirm") or not p.slots.get("_text")):
            return self._end("Thank you for your time. Have a great day!", self.lead.status)
        return self._next_prompt(prefix)

    # ------------------------------------------------------------------ pieces

    def _global_intents(self, p: Parse) -> Turn | None:
        if p.intent == "do_not_call":
            self._log("crm.mark_do_not_call")
            return self._end("Understood. I've removed your number from our calling list. Sorry for the trouble.",
                             "do_not_call")
        if p.intent == "not_interested":
            return self._end("No problem at all. Thank you for your time, and have a good day!", "not_interested")
        if p.intent == "human":
            self._log("handoff_to_human", reason="caller asked")
            return self._end("Sure, I'm connecting you to one of our sales experts now. Please stay on the line.",
                             "handoff")
        if p.intent == "callback" and self.stage != "booked":
            self._log("schedule_callback", day=p.slots.get("visit_day"))
            return self._end("No problem, I'll call you back at a better time. Have a nice day!", "callback")
        return None

    def _contextual(self, p: Parse) -> None:
        """Interpret a bare number using the question the agent just asked."""
        if not self.config.contextual_numbers:
            return
        text = p.slots["_text"]
        numbers = re.findall(r"\b\d+(?:\.\d+)?\b", text)
        if self.expecting == "budget" and "budget" not in p.slots and numbers:
            value = float(numbers[-1])
            lakh = round(value * 100, 1) if value < 10 else value  # "1.2" after "what budget?" means crore
            if 10 <= lakh <= 2000:
                p.slots["budget"] = (None, lakh)
                p.uncertain.add("budget")
        if self.expecting == "bhk" and "bhk" not in p.slots and numbers and numbers[0] in ("1", "2", "3", "4"):
            p.slots["bhk"] = int(numbers[0])
        if self.expecting in ("phone_first5", "phone_last5"):
            chunk = re.findall(r"\b\d{5}\b", text.replace(" ", "")) if len(re.sub(r"\D", "", text)) == 5 else []
            if chunk and self.expecting == "phone_first5":
                if chunk[0][0] in "6789":
                    self.slots["_phone_first"] = chunk[0]
            elif chunk:
                p.slots["phone"] = self.slots.pop("_phone_first") + chunk[0]
        if self.expecting == "name" and "name" not in p.slots:
            words = [w for w in re.findall(r"[a-z]+", text) if w not in NOT_NAMES]
            if len(words) == 1 or (words and len(text.split()) <= 3):
                p.slots["name"] = words[-1].capitalize()

    def _plausible_budget(self, budget, bhk: int | None) -> bool:
        """Is this budget in the range people realistically quote for this BHK in this catalogue?"""
        prices = [c.price_min_lakh for pr in self.catalog.projects for c in pr.configs if bhk is None or c.bhk == bhk]
        return min(prices) * 0.5 <= budget[1] <= max(prices) * 1.5

    def _absorb_slots(self, p: Parse) -> str | Turn:
        """Store what the caller told us. Returns a short acknowledgement, or a confirmation question."""
        self._contextual(p)
        if self.config.confirm_uncertain:
            if "budget" in p.slots and not self._plausible_budget(p.slots["budget"],
                                                                   p.slots.get("bhk", self.slots.get("bhk"))):
                p.uncertain.add("budget")
            if "phone" in p.slots:
                p.uncertain.add("phone")  # always read a number back before booking on it
        for slot in ("locality", "bhk", "budget", "timeline", "purpose", "name", "phone", "visit_day"):
            if slot not in p.slots:
                continue
            value = p.slots[slot]
            if slot in p.uncertain and self.config.confirm_uncertain:
                self.pending_confirm = (slot, value)
                return self._say(f"Just to confirm, {self._describe(slot, value)}, right?", f"confirm:{slot}")
            changed = slot in self.slots and self.slots[slot] != value and slot in REQUIRED
            self.slots[slot] = value
            if changed and self.stage in ("recommending", "visit"):
                self.stage = "qualifying"  # requirements changed: search again
                self.presented = []
        return ""

    def _describe(self, slot: str, value) -> str:
        if slot == "budget":
            low, high = value
            return (f"a budget of {lakh_phrase(low)} to {lakh_phrase(high)}" if low else
                    f"a budget of up to {lakh_phrase(high)}")
        if slot == "bhk":
            return f"a {value} BHK"
        if slot == "locality":
            return f"you're looking in {value}"
        if slot == "phone":
            return f"your number ends with {' '.join(value[-4:])}"
        return f"{slot.replace('_', ' ')} {value}"

    def _resolve_confirmation(self, p: Parse) -> Turn | None:
        slot, value = self.pending_confirm
        self.pending_confirm = None
        if p.intent == "affirm" and slot not in p.slots:
            self.slots[slot] = value
            if slot == "phone" and self.stage == "visit":
                return self._visit_step("")
            return None
        if slot == "phone" and p.intent == "affirm" and p.slots.get("phone") == value:
            self.slots[slot] = value
            p.slots.pop("phone")
            return self._visit_step("") if self.stage == "visit" else None
        if slot in p.slots and slot not in p.uncertain:
            return None  # a fresh, clear value: _absorb_slots will store it
        if slot in p.slots:  # a correction we also only half-heard: read the new value back
            self.pending_confirm = (slot, p.slots[slot])
            return self._say(f"Sorry about that. So {self._describe(slot, p.slots[slot])}, right?", f"confirm:{slot}")
        if p.intent == "deny":
            self.slots.pop(slot, None)
            return self._say(f"Sorry about that. {REASK.get(slot, 'Could you repeat that?')}", slot)
        self.slots[slot] = value  # caller moved on without objecting
        return None

    def _recommend(self, prefix: str) -> Turn:
        low, high = self.slots["budget"]
        matches = self.catalog.search(self.slots["locality"], self.slots["bhk"], low, high)
        self._log("search_properties", locality=self.slots["locality"], bhk=self.slots["bhk"], budget_max=high,
                  results=[m.project.id for m in matches])
        if not matches:
            self.presented = []
            return self._end(f"{prefix} I couldn't find a {self.slots['bhk']} BHK in {self.slots['locality']} or "
                             "nearby within that budget right now. I'll note your requirement and our team will call "
                             "you as soon as something matches. Thank you!", "no_match")
        self.presented = matches
        self.stage = "recommending"
        parts = []
        for m in matches:
            where = m.project.locality if m.exact_locality else f"{m.project.locality}, close to {self.slots['locality']}"
            possession = m.project.facts["possession"]
            possession = "ready to move" if possession == "Ready to move" else f"ready {possession}"
            parts.append(f"{m.project.name} in {where}, {lakh_phrase(m.config.price_min_lakh)} "
                         f"to {lakh_phrase(m.config.price_max_lakh)}, {possession}")
        opener = (f"I found a {self.slots['bhk']} BHK for you:" if len(parts) == 1
                  else f"I found two {self.slots['bhk']} BHK options:")
        return self._say(f"{prefix} {opener} {'; and '.join(parts)}. Would you like to book a site visit?".strip(),
                         "visit_consent")

    def _target_project(self, text: str) -> Match:
        for m in self.presented:
            if m.project.name.lower() in text or m.project.name.split()[0].lower() in text.split():
                return m
        return self.presented[0]

    def _answer_question(self, p: Parse, prefix: str) -> Turn:
        if not self.presented:
            return self._next_prompt(prefix + " Happy to answer that once I've found the right project for you.")
        target = self._target_project(p.slots["_text"])
        answer = self.brochure.answer(target.project, p.slots["_text"])
        self._log("answer_from_brochure", project=target.project.id, topic=answer.topic, found=answer.text is not None)
        if answer.topic is None and self.expecting != "repeat_question":
            self.resume_expecting = self.expecting
            return self._say(f"{prefix} Sorry, could you repeat your question?".strip(), "repeat_question")
        if self.expecting == "repeat_question":
            self.expecting = self.resume_expecting
        reply = answer.text or DEFER
        follow = " Would you like to book a site visit?" if self.stage == "recommending" else ""
        if self.stage == "visit":
            nxt = self._visit_step("", dry=True)
            follow = f" {nxt}" if nxt else ""
        return self._say(f"{prefix} {reply}{follow}".strip(),
                         "visit_consent" if self.stage == "recommending" else self.expecting)

    def _visit_step(self, prefix: str, dry: bool = False):
        if self.visit_project is None and self.presented:
            self.visit_project = self._target_project(self.last_text).project.id
        steps = (("visit_day", "Which day suits you for the visit? We're open all week, including weekends."),
                 ("name", "May I have your name, please?"),
                 ("phone", "And the best mobile number to send the confirmation to?"))
        for slot, question in steps:
            if slot not in self.slots:
                expecting = slot
                if slot == "phone" and self.config.chunked_phone and self.phone_attempts >= 1 and \
                        not (self.caller_number and self.phone_attempts == 1 and self.expecting == "phone_on_file"):
                    first = self.slots.get("_phone_first")
                    question = ("Let's take it in two parts. What are the last five digits?" if first else
                                "Let's take it in two parts. What are the first five digits?")
                    expecting = "phone_last5" if first else "phone_first5"
                elif slot == "phone" and self.caller_number and not self.phone_attempts:
                    # Spoken ten-digit numbers are the most error-prone thing on a call. On an outbound call
                    # we already know the number, so offer it instead of asking for dictation.
                    question = (f"Shall I send the confirmation to this number, ending "
                                f"{' '.join(self.caller_number[-4:])}?")
                    expecting = "phone_on_file"
                if dry:
                    return question
                if slot == "phone":
                    self.phone_attempts += 1
                    if self.phone_attempts > 6:
                        self._log("handoff_to_human", reason="could not capture phone number")
                        return self._end("I'm having trouble catching the number. Our sales expert will call you "
                                         "back to confirm the visit. Thank you!", "handoff")
                return self._say(f"{prefix} {question}".strip(), expecting)
        if dry:
            return ""
        project = self.catalog.by_id[self.visit_project]
        self._save()
        self.crm.book_visit(self.lead.id, project.id, self.slots["visit_day"])
        self._log("crm.book_visit", project=project.id, day=self.slots["visit_day"])
        self._save(status="visit_booked")
        self.stage = "booked"
        last4 = self.slots["phone"][-4:]
        return self._say(f"{prefix} Done, {self.slots['name']}. Your site visit to {project.name} is booked for "
                         f"{self.slots['visit_day'].capitalize()}. You'll get a confirmation on your number ending {last4}. "
                         "Is there anything else I can help with?".strip(), "anything_else")
