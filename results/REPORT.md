# Evaluation report

1000 simulated calls per cell, held-out caller seeds 100000 to 100999. Word error rate is the share of caller words the simulated speech recogniser mishears, drops or pads with a filler.

## Task success (every check passed)

| Config | WER 0% | WER 5% | WER 10% | WER 20% |
|---|---|---|---|---|
| baseline | 18.4% | 18.4% | 18.4% | 18.4% |
| no_spoken_numbers | 18.4% | 18.4% | 18.4% | 18.4% |
| no_fuzzy_locality | 99.9% | 99.9% | 99.1% | 94.9% |
| no_confirmations | 99.9% | 89.7% | 80.2% | 65.4% |
| no_chunked_phone | 99.9% | 99.9% | 99.0% | 94.4% |
| full | 99.9% | 99.9% | 99.1% | 94.8% |

## Full agent, detail

| Measure | WER 0% | WER 5% | WER 10% | WER 20% |
|---|---|---|---|---|
| Correct call outcome | 99.9% | 99.9% | 99.6% | 96.4% |
| All four requirements captured | 99.9% | 99.9% | 99.9% | 99.9% |
| Recommendation correct | 99.9% | 99.9% | 99.8% | 99.8% |
| Site-visit booking correct | 100.0% | 100.0% | 99.6% | 93.7% |
| Brochure questions answered correctly | 100.0% | 100.0% | 99.1% | 97.0% |
| Opt-outs, hand-offs and callbacks handled | 100.0% | 100.0% | 100.0% | 100.0% |
| Guardrail violations | 0 | 0 | 0 | 0 |
| Average caller turns | 8.0 | 8.5 | 9.0 | 10.5 |
