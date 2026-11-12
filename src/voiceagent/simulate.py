"""Simulated callers with hidden requirements, for testing the agent at scale.

Each ``Caller`` knows what they want (area, BHK, budget, timeline), how they behave (asks
questions, wants a discount, is busy, not interested, wants a human, asks not to be called)
and answers the agent's questions in varied, natural phrasings. Everything they say passes
through ``NoisyASR`` before the agent hears it, so every test is a test under speech errors.

The caller reads the agent's ``expecting`` field to know what was asked. That is a
simplification: a real caller listens to the words.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from .agent import DEFER, AgentConfig, Call
from .catalog import TOPICS, Catalog, asked_amenity
from .crm import InMemoryCRM
from .voice import NoisyASR, speakable

FIRST_NAMES = ["Ravi", "Priya", "Arjun", "Meera", "Karthik", "Divya", "Rahul", "Ananya", "Vikram", "Sneha", "Rohan",
               "Kavya", "Suresh", "Lakshmi", "Nikhil", "Pooja", "Aditya", "Shreya", "Manoj", "Deepa"]
QUESTION_PHRASES = {
    "possession": ["when can I move in", "what is the possession date", "when will it be ready"],
    "rera": ["is it RERA registered", "is the project RERA approved"],
    "amenities": ["is there a swimming pool", "do you have a gym", "is there a clubhouse", "what amenities are there",
                  "is there a jogging track", "is there a play area for kids"],
    "maintenance": ["what are the maintenance charges", "how much is the monthly maintenance"],
    "parking": ["what about car parking", "how many parking slots do I get"],
    "metro": ["how far is the metro station", "is the metro close by"],
    "payment_plan": ["what is the payment plan", "how do I pay, is there an instalment plan"],
    "home_loan": ["can I get a home loan", "do you have bank loan tie ups"],
    "pets": ["do you allow pets", "is it pet friendly, I have a dog"],
}
TIMELINE_PHRASES = {
    "0-3 months": ["immediately", "within 3 months", "as soon as possible, maybe next month", "in the next 2 months"],
    "3-6 months": ["in about 6 months", "within six months", "in a few months"],
    "6-12 months": ["next year", "within a year", "by the end of the year"],
    "exploring": ["just exploring for now", "no hurry, just looking", "not sure yet, just checking"],
}


@dataclass
class Caller:
    seed: int
    locality: str
    bhk: int
    budget: tuple[float | None, float]     # (min, max) lakh, as the caller means it
    budget_phrase: str
    timeline: str
    purpose: str
    name: str
    phone: str
    visit_day: str
    willing_visit: bool
    questions: list[str]                   # brochure topics they will ask about
    wants_discount: bool
    exit_behaviour: str | None             # None, "busy", "not_interested", "human", "do_not_call"
    chatty: bool                           # volunteers several details in one answer
    on_known_number: bool = True           # calling from the number the agent dialled
    rng: random.Random = field(repr=False, default_factory=random.Random)
    asked: list[tuple[str, str]] = field(default_factory=list)   # (topic, phrase) actually asked
    exit_at: int = 0

    def say(self, expecting: str | None, call: Call) -> str:
        r = self.rng
        if self.exit_behaviour and call.turns + 1 >= self.exit_at:
            return {"busy": "sorry I'm in a meeting, call me later",
                    "not_interested": "I'm not interested anymore, thanks",
                    "human": "can I speak to a real person please",
                    "do_not_call": "please don't call me again, remove my number"}[self.exit_behaviour]
        if expecting == "consent":
            if self.chatty:
                return f"yes sure, I'm looking for a {self.bhk} BHK in {self.locality}, budget {self.budget_phrase}"
            return r.choice(["yes sure", "yes, go ahead", "okay, tell me", "yeah I have a few minutes"])
        if expecting and expecting.startswith("confirm:"):
            slot, value = call.pending_confirm
            truth = {"locality": self.locality, "bhk": self.bhk, "budget": self.budget, "phone": self.phone}.get(slot)
            ok = _budget_match(value, self.budget) if slot == "budget" else value == truth
            if ok:
                return r.choice(["yes", "yes, correct", "that's right"])
            return "no, " + self._answer(slot)
        if expecting in ("locality", "bhk", "budget", "timeline", "name", "phone", "visit_day"):
            return self._answer(expecting)
        if expecting == "phone_first5":
            return self.phone[:5]
        if expecting == "phone_last5":
            return self.phone[5:]
        if expecting == "repeat_question" and self.asked:
            return self.asked[-1][1]
        if expecting == "confirm_summary":
            got = call.slots
            for slot in ("locality", "bhk", "budget", "timeline"):
                value = got.get(slot)
                ok = (_budget_match(value, self.budget) if slot == "budget"
                      else value == getattr(self, slot))
                if not ok:
                    return "no, " + self._answer(slot)
            return self.rng.choice(["yes", "yes, that's right", "correct"])
        if expecting == "correction":
            for slot in ("locality", "bhk", "budget", "timeline"):
                value = call.slots.get(slot)
                ok = _budget_match(value, self.budget) if slot == "budget" else value == getattr(self, slot)
                if not ok:
                    return self._answer(slot)
            return "everything else is right"
        if expecting == "visit_consent":
            pending = [t for t in self.questions if t not in {a for a, _ in self.asked}]
            if pending:
                topic = pending[0]
                phrase = r.choice(QUESTION_PHRASES[topic])
                self.asked.append((topic, phrase))
                return phrase
            if self.wants_discount and not getattr(self, "_discount_asked", False):
                self._discount_asked = True
                return r.choice(["can you give me a discount", "what is the best price, any offer?"])
            if self.willing_visit:
                return r.choice(["yes, let's book a visit", "sure, I'd like to see it", f"yes, {self.visit_day} works"])
            return r.choice(["no, not right now", "no thanks, just send me details"])
        if expecting == "phone_on_file":
            if self.on_known_number:
                return self.rng.choice(["yes", "yes, this number is fine", "yes please"])
            return "no, " + self._answer("phone")
        if expecting == "anything_else":
            return r.choice(["no, that's all, thanks", "no thank you"])
        return "okay"

    def _answer(self, slot: str) -> str:
        r = self.rng
        if slot == "locality":
            return r.choice([f"{self.locality}", f"I'm looking in {self.locality}", f"somewhere in {self.locality}",
                             f"{self.locality} area preferably"])
        if slot == "bhk":
            return r.choice([f"{self.bhk} BHK", f"a {self.bhk} bedroom flat", f"{self.bhk} BHK please"])
        if slot == "budget":
            return r.choice([f"{self.budget_phrase}", f"my budget is {self.budget_phrase}"])
        if slot == "timeline":
            return r.choice(TIMELINE_PHRASES[self.timeline])
        if slot == "name":
            return r.choice([f"my name is {self.name}", f"{self.name}", f"this is {self.name}"])
        if slot == "phone":
            return r.choice([f"{self.phone}", f"my number is {self.phone}"])
        if slot == "visit_day":
            return r.choice([f"{self.visit_day}", f"{self.visit_day} is good", f"let's do {self.visit_day}"])
        return "okay"


def _amount_phrase(lakh: float, rng: random.Random) -> str:
    if lakh >= 100:
        crore = round(lakh / 100, 1)
        return f"{crore:g} crore"
    return f"{int(round(lakh))} " + rng.choice(["lakh", "lakhs", "lakh"])


def make_caller(catalog: Catalog, seed: int) -> Caller:
    r = random.Random(seed)
    if r.random() < 0.75:  # usually someone whose wish exists in the catalogue
        project = r.choice(catalog.projects)
        config = r.choice(project.configs)
        locality, bhk = project.locality, config.bhk
        anchor = config.price_max_lakh * r.uniform(0.98, 1.25)
    else:                  # sometimes a wish the catalogue cannot meet
        locality, bhk = r.choice(catalog.localities), r.choice([1, 2, 3, 4])
        anchor = r.uniform(30, 150)
    style = r.random()
    if style < 0.45:
        high = max(20, round(anchor / 5) * 5) if anchor < 100 else round(anchor / 10) * 10
        budget, phrase = (None, float(high)), r.choice(["under ", "up to ", "within ", ""]) + _amount_phrase(high, r)
    elif style < 0.75:
        low = round(anchor * 0.85 / 5) * 5
        high = round(anchor / 5) * 5 if anchor < 100 else round(anchor / 10) * 10
        if high >= 100 and low < 100:
            phrase = f"{_amount_phrase(low, r)} to {_amount_phrase(high, r)}"
        elif high >= 100:
            phrase = f"{round(low / 100, 1):g} to {round(high / 100, 1):g} crore"
            low, high = round(low / 100, 1) * 100, round(high / 100, 1) * 100
        else:
            phrase = f"{int(low)} to {int(high)} lakh"
        budget = (float(low), float(high))
    else:
        mid = round(anchor / 5) * 5 if anchor < 100 else round(anchor / 10) * 10
        phrase = "around " + _amount_phrase(mid, r)
        budget = (round(mid * 0.9, 1), round(mid * 1.1, 1))
    exit_roll = r.random()
    exit_behaviour = None
    if exit_roll < 0.04:
        exit_behaviour = "do_not_call"
    elif exit_roll < 0.10:
        exit_behaviour = "not_interested"
    elif exit_roll < 0.14:
        exit_behaviour = "human"
    elif exit_roll < 0.18:
        exit_behaviour = "busy"
    caller = Caller(
        seed=seed, locality=locality, bhk=bhk, budget=budget, budget_phrase=phrase,
        timeline=r.choice(list(TIMELINE_PHRASES)), purpose=r.choice(["self-use", "investment"]),
        name=r.choice(FIRST_NAMES), phone=str(r.choice([6, 7, 8, 9])) + "".join(str(r.randint(0, 9)) for _ in range(9)),
        visit_day=r.choice(["saturday", "sunday", "tomorrow", "friday"]), willing_visit=r.random() < 0.7,
        questions=r.sample(list(TOPICS), r.choice([0, 0, 1, 1, 2])), wants_discount=r.random() < 0.15,
        exit_behaviour=exit_behaviour, chatty=r.random() < 0.2, rng=r,
    )
    caller.exit_at = r.randint(1, 4)
    caller.on_known_number = r.random() < 0.8
    return caller


# --------------------------------------------------------------------------- scoring


@dataclass
class CallResult:
    seed: int
    turns: int
    outcome: str
    expected_outcome: str
    slots_correct: dict[str, bool]
    recommendation_ok: bool | None   # None when no recommendation was due
    booking_ok: bool | None
    questions: list[bool]            # one entry per brochure question asked
    exit_handled: bool | None
    violations: int
    success: bool


def _close(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= 0.05 * b


def _budget_match(got, truth) -> bool:
    """Both ends of the budget must match: a lost lower bound changes which projects fit."""
    return got is not None and _close(got[0], truth[0]) and _close(got[1], truth[1])


_budget_ok = _budget_match


def run_call(catalog: Catalog, seed: int, error_rate: float, config: AgentConfig | None = None,
             crm: InMemoryCRM | None = None) -> tuple[CallResult, Call]:
    caller = make_caller(catalog, seed)
    asr = NoisyASR(error_rate, seed=seed + 7919)
    crm = crm or InMemoryCRM()
    call = Call(catalog, crm, config or AgentConfig())
    call.caller_number = caller.phone  # outbound call: the agent dialled this number
    turn = call.opening()
    answers: list[tuple[str, str, str]] = []  # (topic, phrase, agent reply) for each question
    exited = False  # did the caller get as far as their exit line before the call ended?
    while not turn.ended:
        asked_before = len(caller.asked)
        utterance = caller.say(turn.expecting, call)
        if caller.exit_behaviour and call.turns + 1 >= caller.exit_at:
            exited = True
        turn = call.respond(asr(utterance))
        if len(caller.asked) > asked_before:
            topic, phrase = caller.asked[-1]
            answers.append((topic, phrase, turn.text))
        elif answers and utterance == answers[-1][1]:  # the caller repeated a question; score the new answer
            answers[-1] = (answers[-1][0], utterance, turn.text)
    lead = crm.get(call.lead.id)

    # What should have happened?
    truth_matches = catalog.search(caller.locality, caller.bhk, caller.budget[0], caller.budget[1], limit=50)
    if caller.exit_behaviour and exited:
        expected = {"busy": "callback", "not_interested": "not_interested", "human": "handoff",
                    "do_not_call": "do_not_call"}[caller.exit_behaviour]
    elif not truth_matches:
        expected = "no_match"
    elif caller.willing_visit:
        expected = "visit_booked"
    else:
        expected = "qualified"

    slots_correct = {}
    if expected not in ("callback", "not_interested", "handoff", "do_not_call"):
        slots_correct = {
            "locality": call.slots.get("locality") == caller.locality,
            "bhk": call.slots.get("bhk") == caller.bhk,
            "budget": _budget_ok(call.slots.get("budget"), caller.budget),
            "timeline": call.slots.get("timeline") == caller.timeline,
        }
    recommendation_ok = None
    if expected in ("visit_booked", "qualified"):
        truth_ids = {m.project.id for m in truth_matches}
        recommendation_ok = bool(call.presented) and all(m.project.id in truth_ids for m in call.presented)
    elif expected == "no_match":
        recommendation_ok = lead.status == "no_match"
    booking_ok = None
    if expected == "visit_booked":
        booking_ok = (lead.visit is not None and lead.phone == caller.phone and lead.name == caller.name
                      and lead.visit["day"] == caller.visit_day)

    question_ok = []
    for topic, phrase, reply in answers:
        project = (call.presented[0].project if call.presented else None)
        # The agent answers about the project the caller names, else the first one presented.
        fact = project.facts.get(topic) if project else None
        amenity = asked_amenity(phrase) if topic == "amenities" else None
        if topic == "amenities" and amenity:
            has = amenity in fact
            question_ok.append(reply.startswith("Yes") if has else reply.startswith("No"))
        elif fact is None:
            question_ok.append(DEFER.split(",")[0] in reply)
        else:
            spoken = speakable(fact).lower().split(",")[0]
            question_ok.append(spoken in reply.lower() or (fact == "Ready to move" and "ready to move in" in reply.lower()))
    exit_handled = None
    if caller.exit_behaviour and exited:
        exit_handled = lead.status == expected and (expected != "do_not_call" or lead.status == "do_not_call")

    outcome = lead.status
    success = (outcome == expected and all(slots_correct.values()) and recommendation_ok is not False
               and booking_ok is not False and all(question_ok) and not call.violations)
    result = CallResult(seed, call.turns, outcome, expected, slots_correct, recommendation_ok, booking_ok, question_ok,
                        exit_handled, len(call.violations), success)
    return result, call
