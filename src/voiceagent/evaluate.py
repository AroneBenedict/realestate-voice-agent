"""Run simulated calls and report how the agent does.

    python -m voiceagent.evaluate                  # held-out test callers, all configs and noise levels
    python -m voiceagent.evaluate --calls 200      # quicker

Development work used caller seeds 0-9,999. The reported numbers use seeds from 100,000 up,
which were not looked at while the agent was being changed.
"""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from dataclasses import asdict
from pathlib import Path

import numpy as np

from .agent import AgentConfig
from .catalog import Catalog
from .simulate import CallResult, run_call

CONFIGS = {
    "baseline": AgentConfig(spoken_numbers=False, fuzzy_locality=False, confirm_uncertain=False,
                            contextual_numbers=False, confirm_summary=False, chunked_phone=False),
    "no_spoken_numbers": AgentConfig(spoken_numbers=False),
    "no_fuzzy_locality": AgentConfig(fuzzy_locality=False),
    "no_confirmations": AgentConfig(confirm_uncertain=False, confirm_summary=False),
    "no_chunked_phone": AgentConfig(chunked_phone=False),
    "full": AgentConfig(),
}
NOISE = (0.0, 0.05, 0.10, 0.20)
TEST_SEED_START = 100_000


def _rate(values: list[bool]) -> float | None:
    return round(float(np.mean(values)), 4) if values else None


def summarise(results: list[CallResult]) -> dict:
    slots = Counter()
    slot_n = Counter()
    for r in results:
        for slot, ok in r.slots_correct.items():
            slot_n[slot] += 1
            slots[slot] += ok
    exits = [r for r in results if r.exit_handled is not None]
    return {
        "calls": len(results),
        "task_success": _rate([r.success for r in results]),
        "outcome_correct": _rate([r.outcome == r.expected_outcome for r in results]),
        "slot_accuracy": {s: round(slots[s] / slot_n[s], 4) for s in slot_n},
        "all_slots_correct": _rate([all(r.slots_correct.values()) for r in results if r.slots_correct]),
        "recommendation_correct": _rate([r.recommendation_ok for r in results if r.recommendation_ok is not None]),
        "booking_correct": _rate([r.booking_ok for r in results if r.booking_ok is not None]),
        "questions_answered_correctly": _rate([ok for r in results for ok in r.questions]),
        "questions_asked": sum(len(r.questions) for r in results),
        "exits_handled": _rate([r.exit_handled for r in exits]),
        "guardrail_violations": int(sum(r.violations for r in results)),
        "avg_turns": round(float(np.mean([r.turns for r in results])), 2),
    }


def evaluate(catalog: Catalog, calls: int, start: int, configs: dict | None = None, noise: tuple | None = None) -> dict:
    configs = CONFIGS if configs is None else configs
    noise = NOISE if noise is None else noise
    out: dict = {}
    for name, config in configs.items():
        out[name] = {}
        for rate in noise:
            results = [run_call(catalog, start + i, rate, config)[0] for i in range(calls)]
            out[name][f"{rate:.2f}"] = summarise(results)
    return out


def failures(catalog: Catalog, calls: int, start: int, rate: float, config: AgentConfig) -> Counter:
    """Group failed calls by their first visible symptom (used during development)."""
    found = Counter()
    for i in range(calls):
        r, _ = run_call(catalog, start + i, rate, config)
        if r.success:
            continue
        if r.outcome != r.expected_outcome:
            found[f"outcome {r.expected_outcome} -> {r.outcome}"] += 1
        elif not all(r.slots_correct.values()):
            found["wrong slot: " + ",".join(s for s, ok in r.slots_correct.items() if not ok)] += 1
        elif r.recommendation_ok is False:
            found["wrong recommendation"] += 1
        elif r.booking_ok is False:
            found["wrong booking details"] += 1
        elif not all(r.questions):
            found["wrong answer to a question"] += 1
        else:
            found["guardrail violation"] += 1
    return found


def render(result: dict) -> str:
    calls = result["calls_per_cell"]
    lines = [
        "# Evaluation report", "",
        f"{calls} simulated calls per cell, held-out caller seeds {TEST_SEED_START} to "
        f"{TEST_SEED_START + calls - 1}. Word error rate is the share of caller words the simulated speech "
        "recogniser mishears, drops or pads with a filler.", "",
        "## Task success (every check passed)", "",
        "| Config | " + " | ".join(f"WER {float(k):.0%}" for k in result["results"]["full"]) + " |",
        "|---|" + "---|" * len(result["results"]["full"]),
    ]
    for name, cells in result["results"].items():
        lines.append(f"| {name} | " + " | ".join(f"{c['task_success']:.1%}" for c in cells.values()) + " |")
    lines += ["", "## Full agent, detail", "",
              "| Measure | " + " | ".join(f"WER {float(k):.0%}" for k in result["results"]["full"]) + " |",
              "|---|" + "---|" * len(result["results"]["full"])]
    full = result["results"]["full"]
    rows = [("Correct call outcome", "outcome_correct"), ("All four requirements captured", "all_slots_correct"),
            ("Recommendation correct", "recommendation_correct"), ("Site-visit booking correct", "booking_correct"),
            ("Brochure questions answered correctly", "questions_answered_correctly"),
            ("Opt-outs, hand-offs and callbacks handled", "exits_handled")]
    for label, key in rows:
        lines.append(f"| {label} | " + " | ".join("n/a" if c[key] is None else f"{c[key]:.1%}" for c in full.values()) + " |")
    lines.append("| Guardrail violations | " + " | ".join(str(c["guardrail_violations"]) for c in full.values()) + " |")
    lines.append("| Average caller turns | " + " | ".join(f"{c['avg_turns']:.1f}" for c in full.values()) + " |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> dict:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--calls", type=int, default=1000)
    parser.add_argument("--out", type=Path, default=Path("results"))
    args = parser.parse_args(argv)
    began = time.perf_counter()
    catalog = Catalog.load()
    result = {"calls_per_cell": args.calls, "test_seed_start": TEST_SEED_START,
              "configs": {k: asdict(v) for k, v in CONFIGS.items()}, "word_error_rates": list(NOISE),
              "results": evaluate(catalog, args.calls, TEST_SEED_START)}
    result["seconds"] = round(time.perf_counter() - began, 1)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "metrics.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    (args.out / "REPORT.md").write_text(render(result), encoding="utf-8")
    for name, cells in result["results"].items():
        print(f"{name:20s} " + "  ".join(f"{k}:{c['task_success']:.3f}" for k, c in cells.items()))
    print(f"wrote {args.out / 'metrics.json'} and {args.out / 'REPORT.md'} in {result['seconds']}s")
    return result


if __name__ == "__main__":
    main()
