import json

from fastapi.testclient import TestClient

from voiceagent.api import create_app
from voiceagent.evaluate import CONFIGS, main, summarise
from voiceagent.simulate import make_caller, run_call


def test_callers_are_reproducible_and_varied(catalog):
    a, b = make_caller(catalog, 5), make_caller(catalog, 5)
    assert (a.locality, a.budget, a.phone) == (b.locality, b.budget, b.phone)
    callers = [make_caller(catalog, i) for i in range(200)]
    assert len({c.locality for c in callers}) == 11
    assert {c.exit_behaviour for c in callers} >= {None, "busy", "not_interested", "human", "do_not_call"}


def test_clean_speech_calls_all_succeed(catalog):
    results = [run_call(catalog, i, 0.0)[0] for i in range(60)]
    assert all(r.success for r in results), [r for r in results if not r.success][:2]


def test_confirmations_matter_under_noise(catalog):
    full = summarise([run_call(catalog, i, 0.2, CONFIGS["full"])[0] for i in range(80)])
    bare = summarise([run_call(catalog, i, 0.2, CONFIGS["no_confirmations"])[0] for i in range(80)])
    assert full["task_success"] > bare["task_success"]
    assert full["guardrail_violations"] == 0


def test_evaluate_cli_writes_report(tmp_path, monkeypatch):
    monkeypatch.setattr("voiceagent.evaluate.NOISE", (0.0, 0.1))
    monkeypatch.setattr("voiceagent.evaluate.CONFIGS", {"baseline": CONFIGS["baseline"], "full": CONFIGS["full"]})
    result = main(["--calls", "20", "--out", str(tmp_path)])
    saved = json.loads((tmp_path / "metrics.json").read_text())
    assert saved["results"] == result["results"] and set(saved["results"]["full"]) == {"0.00", "0.10"}
    assert (tmp_path / "REPORT.md").read_text().startswith("# Evaluation report")


def test_api_end_to_end(catalog):
    client = TestClient(create_app(catalog))
    start = client.post("/calls", json={"caller_number": "9876501234"}).json()
    cid = start["call_id"]
    assert start["expecting"] == "consent"
    for line in ["yes", "whitefield", "2 bhk", "under 1.1 crore", "within 3 months", "yes", "yes", "sunday",
                 "my name is kavya", "yes"]:
        reply = client.post(f"/calls/{cid}/turn", json={"text": line}).json()
    assert "booked" in reply["reply"]
    end = client.post(f"/calls/{cid}/turn", json={"text": "no thanks"}).json()
    assert end["ended"]
    assert client.post(f"/calls/{cid}/turn", json={"text": "hi"}).status_code == 409
    detail = client.get(f"/calls/{cid}").json()
    assert detail["stage"] == "closed" and "phone" not in detail["slots"]
    leads = client.get("/leads").json()
    assert leads[0]["phone"] == "XXXXXX1234" and leads[0]["status"] == "visit_booked"
    metrics = client.get("/metrics").text
    assert 'event="outcome_visit_booked"} 1' in metrics and 'event="calls_started"} 1' in metrics
    assert client.get("/calls/nope").status_code == 404
    assert client.post("/calls", json={"caller_number": "123"}).status_code == 422
    assert client.get("/health").json()["projects"] == 22
