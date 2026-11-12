"""Property catalogue and the agent's data tools: search listings, answer brochure questions."""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DATA = Path(__file__).resolve().parents[2] / "data" / "projects.json"

# Localities a buyer will usually also consider. Used only when nothing matches the first choice.
NEARBY: dict[str, tuple[str, ...]] = {
    "Whitefield": ("KR Puram", "Sarjapur Road"),
    "KR Puram": ("Whitefield", "Hennur"),
    "Sarjapur Road": ("HSR Layout", "Whitefield"),
    "HSR Layout": ("Sarjapur Road", "Bannerghatta Road"),
    "Electronic City": ("Bannerghatta Road", "Sarjapur Road"),
    "Bannerghatta Road": ("Kanakapura Road", "Electronic City"),
    "Kanakapura Road": ("Bannerghatta Road",),
    "Hebbal": ("Hennur", "Yelahanka"),
    "Hennur": ("Hebbal", "KR Puram"),
    "Yelahanka": ("Hebbal", "Devanahalli"),
    "Devanahalli": ("Yelahanka",),
}


@dataclass(frozen=True)
class Config:
    bhk: int
    sqft_min: int
    sqft_max: int
    price_min_lakh: float
    price_max_lakh: float


@dataclass(frozen=True)
class Project:
    id: str
    name: str
    developer: str
    locality: str
    zone: str
    units_available: int
    configs: tuple[Config, ...]
    facts: dict

    def config(self, bhk: int) -> Config | None:
        return next((c for c in self.configs if c.bhk == bhk), None)


@dataclass(frozen=True)
class Match:
    project: Project
    config: Config
    exact_locality: bool
    within_budget: bool  # False means it is a stretch of up to 10% over budget


class Catalog:
    def __init__(self, projects: list[Project]):
        self.projects = projects
        self.by_id = {p.id: p for p in projects}
        self.localities = sorted({p.locality for p in projects})

    @classmethod
    def load(cls, path: Path | str = DEFAULT_DATA) -> Catalog:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        projects = [
            Project(p["id"], p["name"], p["developer"], p["locality"], p["zone"], p["units_available"],
                    tuple(Config(**c) for c in p["configs"]), dict(p["facts"]))
            for p in raw["projects"]
        ]
        return cls(projects)

    def search(self, locality: str, bhk: int, budget_min: float | None, budget_max: float,
               include_nearby: bool = True, stretch: float = 0.10, limit: int = 2) -> list[Match]:
        """Projects with the requested configuration whose price range overlaps the budget.

        Exact-locality matches come first. Nearby localities are tried only if the first choice
        has nothing. A price up to ``stretch`` above the budget is allowed but marked as a stretch.
        """
        def matches(localities: tuple[str, ...], exact: bool) -> list[Match]:
            found = []
            for project in self.projects:
                if project.locality not in localities or project.units_available == 0:
                    continue
                config = project.config(bhk)
                if config is None:
                    continue
                low = budget_min if budget_min is not None else 0.0
                if config.price_min_lakh > budget_max * (1 + stretch) or config.price_max_lakh < low:
                    continue
                found.append(Match(project, config, exact, config.price_min_lakh <= budget_max))
            return sorted(found, key=lambda m: (not m.within_budget, m.config.price_min_lakh))

        found = matches((locality,), True)
        if not found and include_nearby:
            found = matches(NEARBY.get(locality, ()), False)
        return found[:limit]


# --------------------------------------------------------------------------- brochure Q&A

TOPICS: dict[str, tuple[str, ...]] = {
    "possession": ("possession", "ready", "move", "handover", "completion", "complete", "when"),
    "rera": ("rera", "registration", "registered", "approval", "approved"),
    "amenities": ("amenities", "amenity", "facilities", "pool", "swimming", "gym", "clubhouse", "club", "play", "kids",
                  "jogging", "track", "badminton", "garden", "games"),
    "maintenance": ("maintenance", "monthly", "charges"),
    "parking": ("parking", "car", "park"),
    "metro": ("metro", "station", "transport", "connectivity"),
    "payment_plan": ("payment", "plan", "instalment", "installment", "pay"),
    "home_loan": ("loan", "bank", "finance", "emi"),
    "pets": ("pet", "pets", "dog", "dogs", "cat", "animals"),
}
_TOKEN = re.compile(r"[a-z]+")


class BrochureIndex:
    """BM25 over topic keyword profiles. Finds which brochure fact a question is about.

    It never generates an answer: the reply is the stored fact, or a hand-off when the brochure
    has no such fact or the question matches no topic well enough.
    """

    def __init__(self, k1: float = 1.2, b: float = 0.5, min_score: float = 1.0):
        self.k1, self.b, self.min_score = k1, b, min_score
        self.docs = {topic: Counter(words) for topic, words in TOPICS.items()}
        n = len(self.docs)
        df = Counter(word for doc in self.docs.values() for word in doc)
        self.idf = {w: math.log(1 + (n - c + 0.5) / (c + 0.5)) for w, c in df.items()}
        self.avg = sum(sum(d.values()) for d in self.docs.values()) / n

    def topic(self, question: str) -> tuple[str | None, float]:
        words = Counter(_TOKEN.findall(question.lower()))
        best, best_score = None, 0.0
        for topic, doc in self.docs.items():
            length = sum(doc.values())
            score = 0.0
            for word in words:
                tf = doc.get(word, 0)
                if tf:
                    norm = self.k1 * (1 - self.b + self.b * length / self.avg)
                    score += self.idf[word] * tf * (self.k1 + 1) / (tf + norm)
            if score > best_score:
                best, best_score = topic, score
        return (best, round(best_score, 3)) if best_score >= self.min_score else (None, round(best_score, 3))

    def answer(self, project: Project, question: str) -> Answer:
        """Answer from the brochure only. ``text`` is None when the brochure does not say."""
        topic, _ = self.topic(question)
        if topic is None:
            return Answer(None, None, None)
        fact = project.facts.get(topic)
        if fact is None:
            return Answer(topic, None, None)
        name = project.name
        if topic == "amenities":
            asked = asked_amenity(question)
            if asked:
                has = asked in fact
                text = (f"Yes, {name} has a {asked}." if has else
                        f"No, the brochure for {name} doesn't list a {asked}. It has {fact}.")
                return Answer(topic, fact, text, asked=asked, has=has)
            return Answer(topic, fact, f"{name} has {fact}.")
        templates = {
            "possession": "{name} is ready to move in." if fact == "Ready to move" else "Possession for {name} is {fact}.",
            "rera": "Yes, {name} is RERA registered. The number is {fact}.",
            "maintenance": "Maintenance at {name} is {fact}.",
            "parking": "{name} gives {fact}.",
            "metro": "{name} is {fact}.",
            "payment_plan": "{name} has a {fact}.",
            "home_loan": "Yes, {fact}.",
            "pets": "Yes, pets are welcome at {name}: {fact}.",
        }
        return Answer(topic, fact, templates[topic].format(name=name, fact=fact))


AMENITY_WORDS = {
    "swimming pool": ("pool", "swimming"), "gym": ("gym", "fitness"), "clubhouse": ("clubhouse", "club"),
    "children's play area": ("play", "kids", "children"), "jogging track": ("jogging", "running"),
    "badminton court": ("badminton",), "landscaped garden": ("garden", "park"), "co-working space": ("coworking", "co"),
    "power backup": ("backup", "power"), "indoor games room": ("games", "indoor"),
}


def asked_amenity(question: str) -> str | None:
    words = set(_TOKEN.findall(question.lower()))
    for amenity, keys in AMENITY_WORDS.items():
        if words & set(keys):
            return amenity
    return None


@dataclass(frozen=True)
class Answer:
    topic: str | None
    fact: str | None
    text: str | None
    asked: str | None = None   # the specific amenity asked about, if any
    has: bool | None = None
