"""CRM integration. The agent talks to a CRM only through this interface."""

from __future__ import annotations

import itertools
from dataclasses import asdict, dataclass, field
from typing import Protocol


@dataclass
class Lead:
    id: str
    name: str | None = None
    phone: str | None = None
    locality: str | None = None
    bhk: int | None = None
    budget_min_lakh: float | None = None
    budget_max_lakh: float | None = None
    timeline: str | None = None
    purpose: str | None = None
    status: str = "new"            # new, qualified, visit_booked, no_match, not_interested, do_not_call, callback, handoff
    score: str | None = None       # hot, warm, cold
    interested_in: list[str] = field(default_factory=list)
    visit: dict | None = None      # {"project_id": ..., "day": ...}
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


class CRM(Protocol):
    def create_lead(self) -> Lead: ...

    def update_lead(self, lead_id: str, **fields) -> Lead: ...

    def book_visit(self, lead_id: str, project_id: str, day: str) -> Lead: ...

    def get(self, lead_id: str) -> Lead: ...


class InMemoryCRM:
    """A stand-in for a real CRM (Salesforce, Zoho, LeadSquared...). Same calls, kept in memory."""

    def __init__(self) -> None:
        self.leads: dict[str, Lead] = {}
        self._ids = itertools.count(1)

    def create_lead(self) -> Lead:
        lead = Lead(id=f"L{next(self._ids):05d}")
        self.leads[lead.id] = lead
        return lead

    def get(self, lead_id: str) -> Lead:
        if lead_id not in self.leads:
            raise KeyError(f"unknown lead {lead_id}")
        return self.leads[lead_id]

    def update_lead(self, lead_id: str, **fields) -> Lead:
        lead = self.get(lead_id)
        for key, value in fields.items():
            if not hasattr(lead, key) or key == "id":
                raise ValueError(f"unknown lead field {key!r}")
            setattr(lead, key, value)
        return lead

    def book_visit(self, lead_id: str, project_id: str, day: str) -> Lead:
        lead = self.get(lead_id)
        if not lead.phone:
            raise ValueError("a site visit needs a contact number")
        if lead.status == "do_not_call":
            raise ValueError("lead has opted out of contact")
        lead.visit = {"project_id": project_id, "day": day}
        lead.status = "visit_booked"
        return lead
