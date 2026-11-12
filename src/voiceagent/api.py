"""HTTP service for the agent. A telephony or voice platform calls these endpoints.

    uvicorn voiceagent.api:app --port 8000

    POST /calls                  start a call, get the opening line
    POST /calls/{id}/turn        send what the caller said (transcribed), get the reply
    GET  /calls/{id}             transcript (phone numbers masked), state and tool trace
    GET  /leads                  leads captured so far
    GET  /metrics                Prometheus-format counters
"""

from __future__ import annotations

import time
import uuid
from collections import Counter

from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from . import __version__
from .agent import Call
from .catalog import Catalog
from .crm import InMemoryCRM


class StartCall(BaseModel):
    caller_number: str | None = Field(None, pattern=r"^[6-9]\d{9}$")


class CallerTurn(BaseModel):
    text: str = Field(..., max_length=1000)


def create_app(catalog: Catalog | None = None) -> FastAPI:
    app = FastAPI(title="Real-estate voice agent", version=__version__)
    catalog = catalog or Catalog.load()
    crm = InMemoryCRM()
    calls: dict[str, Call] = {}
    counters: Counter = Counter()
    latency: Counter = Counter()

    def get_call(call_id: str) -> Call:
        if call_id not in calls:
            raise HTTPException(404, f"call {call_id} not found")
        return calls[call_id]

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "version": __version__, "projects": len(catalog.projects), "active_calls":
                sum(not c.ended for c in calls.values())}

    @app.post("/calls")
    def start(body: StartCall | None = None) -> dict:
        call = Call(catalog, crm)
        call.caller_number = body.caller_number if body else None
        call_id = uuid.uuid4().hex[:12]
        calls[call_id] = call
        counters["calls_started"] += 1
        turn = call.opening()
        return {"call_id": call_id, "reply": turn.text, "expecting": turn.expecting, "ended": False}

    @app.post("/calls/{call_id}/turn")
    def turn(call_id: str, body: CallerTurn) -> dict:
        call = get_call(call_id)
        if call.ended:
            raise HTTPException(409, "call has ended")
        began = time.perf_counter()
        result = call.respond(body.text)
        latency["seconds"] += time.perf_counter() - began
        counters["turns"] += 1
        if result.ended:
            counters[f"outcome_{crm.get(call.lead.id).status}"] += 1
        return {"reply": result.text, "expecting": result.expecting, "ended": result.ended}

    @app.get("/calls/{call_id}")
    def detail(call_id: str) -> dict:
        call = get_call(call_id)
        return {"lead_id": call.lead.id, "stage": call.stage, "ended": call.ended,
                "slots": {k: v for k, v in call.slots.items() if not k.startswith("_") and k != "phone"},
                "transcript": [{"speaker": s, "text": t} for s, t in call.transcript],
                "trace": [{"turn": e.turn, "tool": e.tool, **e.detail} for e in call.trace],
                "guardrail_violations": [v.rule for v in call.violations]}

    @app.get("/leads")
    def leads() -> list[dict]:
        out = []
        for lead in crm.leads.values():
            item = lead.to_dict()
            if item["phone"]:
                item["phone"] = "XXXXXX" + item["phone"][-4:]
            out.append(item)
        return out

    @app.get("/metrics", response_class=PlainTextResponse)
    def metrics() -> str:
        lines = ["# TYPE voiceagent_events_total counter"]
        lines += [f'voiceagent_events_total{{event="{k}"}} {v}' for k, v in sorted(counters.items())]
        lines += ["# TYPE voiceagent_turn_seconds_sum counter", f"voiceagent_turn_seconds_sum {latency['seconds']:.6f}"]
        return "\n".join(lines) + "\n"

    return app


app = create_app()
