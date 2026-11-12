"""Understanding caller utterances: intents and slots (locality, BHK, budget, timeline, ...).

Speech-to-text output is lower-case, unpunctuated and full of spelled-out numbers and
misheard words ("white feel", "eighty five lacs", "nine eight four ..."). The parser handles
that in two layers that can be switched off for the ablation study:

* ``spoken_numbers``: turn number words into digits before matching.
* ``fuzzy_locality``: match locality names approximately and through known mishearings.

A match found only approximately is flagged ``uncertain`` so the agent can confirm it.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

UNITS = {"zero": 0, "oh": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
         "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
         "seventeen": 17, "eighteen": 18, "nineteen": 19}
TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
SCALE = {"hundred": 100}

LOCALITY_ALIASES: dict[str, tuple[str, ...]] = {
    "Whitefield": ("whitefield", "white field", "white feel", "white fields", "wait field"),
    "KR Puram": ("kr puram", "k r puram", "krishnarajapuram", "car puram", "kay are puram"),
    "Sarjapur Road": ("sarjapur", "sarjapur road", "sarjapura", "sarja pur", "surja pur"),
    "HSR Layout": ("hsr", "hsr layout", "h s r layout", "h s r", "hs are layout"),
    "Electronic City": ("electronic city", "electronics city", "e city", "ecity", "electric city"),
    "Bannerghatta Road": ("bannerghatta", "bannerghatta road", "banner ghatta", "bannerghatta road", "banner gatta"),
    "Kanakapura Road": ("kanakapura", "kanakapura road", "kanaka pura", "canaca pura"),
    "Hebbal": ("hebbal", "hebal", "hey bal", "hebble"),
    "Hennur": ("hennur", "hen nur", "hennoor", "henoor"),
    "Yelahanka": ("yelahanka", "yela hanka", "yelahanka new town", "yellow hanka"),
    "Devanahalli": ("devanahalli", "devana halli", "devanhalli", "deva nahalli", "airport side"),
}

INTENT_PATTERNS: dict[str, tuple[str, ...]] = {
    "do_not_call": (r"\bdon'?t call\b", r"\bdo not call\b", r"\bremove my number\b", r"\bstop calling\b"),
    "not_interested": (r"\bnot interested\b", r"\bno longer looking\b", r"\balready bought\b", r"\bnot looking\b"),
    "callback": (r"\bcall (me )?(back )?later\b", r"\bbusy\b", r"\bnot a good time\b", r"\bin a meeting\b",
                 r"\bdriving\b"),
    "human": (r"\b(talk|speak) to (a |an )?(human|person|agent|executive|someone|manager)\b", r"\breal person\b",
              r"\bsales (person|executive|manager)\b"),
    "discount": (r"\bdiscount\b", r"\boffer\b", r"\bbest price\b", r"\bnegotiat", r"\breduce the price\b",
                 r"\bcheaper deal\b"),
    "legal": (r"\blegal\b", r"\blawyer\b", r"\btitle (deed|clear)\b", r"\bdispute\b", r"\btax (advice|saving)\b",
              r"\breturns? (guaranteed|assured)\b", r"\bguaranteed returns?\b"),
    "site_visit": (r"\bsite visit\b", r"\bvisit (the )?(site|project|property)\b", r"\bsee the (flat|property|site)\b",
                   r"\bbook a visit\b", r"\bcome and see\b"),
    "question": (r"\bwhat\b", r"\bwhen\b", r"\bhow\b", r"\bis there\b", r"\bare there\b", r"\bdo you\b",
                 r"\bdoes it\b", r"\bcan i\b", r"^\s*is (it|the|this)\b", r"\bwill (it|i)\b", r"\bany\b",
                 r"\btell me about\b"),
    "deny": (r"^\s*(no|nope|nah)\b", r"\bnot really\b", r"\bthat'?s wrong\b", r"\bincorrect\b", r"\bno it'?s\b"),
    "affirm": (r"^\s*(yes|yeah|yep|sure|ok|okay|correct|right|haan|ha)\b", r"\bthat'?s right\b", r"\bsounds good\b",
               r"\bgo ahead\b", r"\bplease do\b", r"\bfine\b"),
    "off_topic": (r"\bweather\b", r"\bcricket\b", r"\bjoke\b", r"\bpolitics\b", r"\bmovie\b"),
}
# Checked in this order; the first matching intent wins, except that slot information is always kept.
INTENT_ORDER = ("do_not_call", "not_interested", "human", "callback", "legal", "discount", "site_visit", "question",
                "deny", "affirm", "off_topic")

DAYS = ("today", "tomorrow", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
        "this weekend", "next weekend")


@dataclass
class Parse:
    intent: str | None = None
    slots: dict = field(default_factory=dict)
    uncertain: set = field(default_factory=set)  # slots found only approximately


def words_to_digits(text: str) -> str:
    """'eighty five lakhs' -> '85 lakhs'; 'one point two crore' -> '1.2 crore'; spelled phone digits joined."""
    tokens = text.split()
    out: list[str] = []
    i = 0
    while i < len(tokens):
        word = tokens[i]
        if word in UNITS or word in TENS:
            value, j = 0, i
            if tokens[j] in TENS:
                value = TENS[tokens[j]]
                j += 1
                if j < len(tokens) and tokens[j] in UNITS and 0 < UNITS[tokens[j]] < 10:
                    value += UNITS[tokens[j]]
                    j += 1
            else:
                value = UNITS[tokens[j]]
                j += 1
                if j < len(tokens) and tokens[j] in SCALE:
                    value *= SCALE[tokens[j]]
                    j += 1
            if j + 1 < len(tokens) and tokens[j] == "point" and tokens[j + 1] in UNITS:
                out.append(f"{value}.{UNITS[tokens[j + 1]]}")
                i = j + 2
                continue
            out.append(str(value))
            i = j
            continue
        if word in ("half", "a") and i + 2 < len(tokens) and tokens[i + 1] in ("a", "half") and tokens[i + 2] in ("crore", "cr"):
            out.append("0.5")  # "half a crore"
            i += 2
            continue
        out.append(word)
        i += 1
    joined = " ".join(out)
    # Spoken phone numbers come out as single digits separated by spaces; glue runs of 6+ digits.
    return re.sub(r"\b(?:\d\s){3,}\d\b", lambda m: m.group(0).replace(" ", ""), joined)


def normalise(text: str) -> str:
    text = text.lower().replace("₹", " rs ")
    text = re.sub(r"(\d),(\d)", r"\1\2", text)
    text = re.sub(r"[^a-z0-9.'\s]", " ", text)
    text = re.sub(r"(?<=\d)(?=[a-z])", " ", text)  # "85lakh" -> "85 lakh", "2bhk" -> "2 bhk"
    text = re.sub(r"\b(uh+|um+|hmm+|er+|basically|actually|like|so)\b", " ", text)  # speech fillers
    return re.sub(r"\s+", " ", text).strip().rstrip(".")


def garbled_amount(text: str) -> bool:
    """Signs that speech recognition broke a spoken amount: '1 3 crore', 'point 3 crore', '1 point crore'."""
    return bool(re.search(r"\b\d+\s+\d+(?:\.\d+)?\s*(lakhs?|lacs?|lac|lack|crores?|cr)\b", text)
                or re.search(r"(?<![\d.])\s*\bpoint\s+\d", text)
                or re.search(r"\b\d+\s+point\s+(lakhs?|crores?|cr)\b", text))


_AMOUNT = r"(\d+(?:\.\d+)?)\s*(lakhs?|lacs?|lac|lack|l|crores?|cr)\b"


def _to_lakh(number: str, unit: str) -> float:
    value = float(number)
    return round(value * 100, 1) if unit.startswith("cr") else value


def parse_budget(text: str) -> tuple[float | None, float] | None:
    """(min, max) in lakh, or None. Handles ranges, 'under', 'around' and a shared trailing unit."""
    between = re.search(r"(\d+(?:\.\d+)?)\s*(lakhs?|lacs?|lac|lack|crores?|cr)?\s*(?:to|and|-)\s*" + _AMOUNT, text)
    if between:
        unit_a = between.group(2) or between.group(4)
        low, high = _to_lakh(between.group(1), unit_a), _to_lakh(between.group(3), between.group(4))
        if low < high:
            return low, high
    single = re.search(r"(under|below|within|upto|up to|max|maximum|less than|around|about|approx\w*|roughly)?\s*"
                       + _AMOUNT, text)
    if not single:
        return None
    amount = _to_lakh(single.group(2), single.group(3))
    if amount < 10 or amount > 2000:  # not a plausible flat budget in lakh
        return None
    qualifier = single.group(1) or ""
    if qualifier in ("around", "about", "roughly") or qualifier.startswith("approx"):
        return round(amount * 0.9, 1), round(amount * 1.1, 1)
    return None, amount


def parse_bhk(text: str) -> int | None:
    match = re.search(r"\b([1-4])\s*(?:bhk|b h k|bedroom|bed room|bed)s?\b", text)
    return int(match.group(1)) if match else None


def parse_locality(text: str, fuzzy: bool = True) -> tuple[str | None, bool]:
    """(locality, uncertain). Exact names first, then known mishearings, then approximate match."""
    for locality, aliases in LOCALITY_ALIASES.items():
        if re.search(r"\b" + re.escape(aliases[0]) + r"\b", text) or (
                locality.lower() in text):
            return locality, False
    if not fuzzy:
        return None, False
    for locality, aliases in LOCALITY_ALIASES.items():
        for alias in aliases[1:]:
            if re.search(r"\b" + re.escape(alias) + r"\b", text):
                return locality, alias.replace(" ", "") != locality.lower().replace(" ", "")
    words = text.split()
    candidates = {alias: loc for loc, aliases in LOCALITY_ALIASES.items() for alias in aliases}
    for size in (2, 1):
        for i in range(len(words) - size + 1):
            phrase = " ".join(words[i:i + size])
            if len(phrase) < 5:
                continue
            close = difflib.get_close_matches(phrase, candidates, n=1, cutoff=0.8)
            if close:
                return candidates[close[0]], True
    return None, False


def parse_timeline(text: str) -> str | None:
    if re.search(r"\b(immediately|right away|asap|urgent|this month|next month|within (a|one|1|2|two|3|three) months?|"
                 r"(1|2|3) months?|ready to move)\b", text):
        return "0-3 months"
    if re.search(r"\b(4|5|6|four|five|six) months\b|\bwithin six months\b|\bhalf a year\b|\bin a few months\b", text):
        return "3-6 months"
    if re.search(r"\b(next year|this year end|end of the year|in a year|within a year|12 months|9 months)\b", text):
        return "6-12 months"
    if re.search(r"\b(just (exploring|looking|checking)|no hurry|not sure yet|browsing|no rush)\b", text):
        return "exploring"
    return None


def parse_purpose(text: str) -> str | None:
    if re.search(r"\b(invest|investment|rental|rent it out|returns)\b", text):
        return "investment"
    if re.search(r"\b(own use|self use|to live|stay|my family|for us|end use|living)\b", text):
        return "self-use"
    return None


def parse_phone(text: str) -> str | None:
    match = re.search(r"(?:\+?91)?\s*\b([6-9]\d{9})\b", text.replace("-", ""))
    return match.group(1) if match else None


NAME_STOPWORDS = {"looking", "interested", "busy", "not", "fine", "good", "calling", "a", "an", "the", "for", "in", "it",
                  "is", "my", "me", "about", "regarding", "from", "just", "only", "very", "really", "okay", "ok", "right",
                  "correct", "wrong", "too", "that", "this", "what", "when", "how", "yes", "no", "sure"}


def parse_name(text: str) -> str | None:
    """Only explicit introductions count: 'I am in a meeting' must not become a name."""
    match = re.search(r"\b(?:my name is|my name'?s|name is|this is)\s+([a-z]+)\b", text)
    if match and match.group(1) not in NAME_STOPWORDS and match.group(1) not in UNITS:
        return match.group(1).capitalize()
    return None


def parse_day(text: str) -> str | None:
    for day in DAYS:
        if re.search(r"\b" + day + r"\b", text):
            return day
    return None


def parse(utterance: str, *, spoken_numbers: bool = True, fuzzy_locality: bool = True) -> Parse:
    text = normalise(utterance)
    if spoken_numbers:
        text = words_to_digits(text)
    result = Parse()
    for intent in INTENT_ORDER:
        if any(re.search(pattern, text) for pattern in INTENT_PATTERNS[intent]):
            result.intent = intent
            break
    locality, uncertain = parse_locality(text, fuzzy=fuzzy_locality)
    if locality:
        result.slots["locality"] = locality
        if uncertain:
            result.uncertain.add("locality")
    if "budget" not in result.slots and garbled_amount(text):
        budget = parse_budget(text)
        if budget is not None:
            result.slots["budget"] = budget
            result.uncertain.add("budget")
    for slot, value in (("bhk", parse_bhk(text)), ("budget", parse_budget(text)), ("timeline", parse_timeline(text)),
                        ("purpose", parse_purpose(text)), ("phone", parse_phone(text)), ("name", parse_name(text)),
                        ("visit_day", parse_day(text))):
        if value is not None and slot not in result.slots:
            result.slots[slot] = value
    result.slots["_text"] = text
    return result
