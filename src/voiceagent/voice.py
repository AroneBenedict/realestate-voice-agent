"""Voice layer: speech in, speech out.

The agent itself works on text. This module is the boundary with a voice stack:

* ``SpeechToText`` / ``TextToSpeech`` are the interfaces a real provider plugs into.
* ``speakable`` rewrites a reply so a text-to-speech engine reads it naturally.
* ``NoisyASR`` imitates speech-recognition errors so the agent can be tested against them.
"""

from __future__ import annotations

import random
import re
from typing import Protocol

UNITS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
         "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


class SpeechToText(Protocol):
    def transcribe(self, audio: bytes) -> str: ...


class TextToSpeech(Protocol):
    def synthesize(self, text: str) -> bytes: ...


def number_words(n: int) -> str:
    if n < 20:
        return UNITS[n]
    if n < 100:
        return TENS[n // 10] + ("" if n % 10 == 0 else " " + UNITS[n % 10])
    if n < 1000:
        rest = n % 100
        return UNITS[n // 100] + " hundred" + ("" if rest == 0 else " " + number_words(rest))
    return str(n)


def lakh_phrase(lakh: float) -> str:
    """92.6 -> '92.6 lakh'; 191.4 -> '1.91 crore' (how Indian buyers say prices)."""
    if lakh >= 100:
        crore = round(lakh / 100, 2)
        return f"{crore:g} crore"
    return f"{round(lakh, 1):g} lakh"


def speakable(text: str) -> str:
    """Make a reply TTS-friendly: no symbols or abbreviations a speech engine would misread."""
    text = text.replace("₹", "").replace("&", " and ")
    text = re.sub(r"\b(\d)\s*BHK\b", lambda m: f"{m.group(1)} B H K", text)
    text = re.sub(r"\bsq\.?\s*ft\b", "square feet", text, flags=re.I)
    text = re.sub(r"\bkm\b", "kilometres", text)
    text = re.sub(r"\bRERA\b", "RERA", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


CONFUSIONS = {  # word -> plausible mishearings
    "lakh": ["lac", "lack", "lakhs"], "lakhs": ["lacs", "lack"], "crore": ["core", "crores"],
    "whitefield": ["white field", "white feel"], "hebbal": ["hebal", "hey bal"], "sarjapur": ["sarja pur"],
    "yelahanka": ["yela hanka"], "hennur": ["hennoor"], "devanahalli": ["devana halli"], "bhk": ["b h k", "bh k"],
    "kanakapura": ["kanaka pura"], "bannerghatta": ["banner ghatta"], "electronic": ["electric", "electronics"],
    "puram": ["pur am"], "possession": ["position"], "parking": ["barking"], "maintenance": ["maintain ants"],
    "metro": ["metre"], "saturday": ["satur day"], "visit": ["visits"], "budget": ["budgets"],
}
FILLERS = ["uh", "um", "so", "like", "actually", "basically"]


def _digits_to_words(text: str) -> str:
    def spell(match: re.Match) -> str:
        token = match.group(0)
        if len(token) >= 4 and "." not in token:  # phone numbers and parts of them are read digit by digit
            return " ".join(UNITS[int(d)] for d in token)
        if "." in token:
            whole, frac = token.split(".")
            return f"{number_words(int(whole))} point {' '.join(UNITS[int(d)] for d in frac)}"
        return number_words(int(token))
    return re.sub(r"\d+(?:\.\d+)?", spell, text)


class NoisyASR:
    """Turns clean caller text into something a speech recogniser might produce.

    Always: lower-case, punctuation dropped, numbers spelled out. Then, per word and with
    probability ``error_rate``, one of: a known mishearing, a dropped word, or an inserted filler.
    """

    def __init__(self, error_rate: float = 0.0, seed: int = 0):
        self.error_rate = error_rate
        self.rng = random.Random(seed)

    def __call__(self, text: str) -> str:
        text = text.lower().replace("₹", "")
        text = re.sub(r"(\d),(\d)", r"\1\2", text)
        text = re.sub(r"(\d)\s*(bhk)", r"\1 \2", text)
        text = _digits_to_words(text)
        words = re.sub(r"[^a-z'\s]", " ", text).split()
        out: list[str] = []
        for word in words:
            if self.error_rate and self.rng.random() < self.error_rate:
                roll = self.rng.random()
                if word in CONFUSIONS and roll < 0.6:
                    out.extend(self.rng.choice(CONFUSIONS[word]).split())
                    continue
                if roll < 0.8:
                    continue  # dropped word
                out.extend([self.rng.choice(FILLERS), word])
                continue
            out.append(word)
        return " ".join(out)
