# Design notes

## The job

A real-estate developer gets hundreds of web enquiries a week. Sales teams cannot call them
all quickly, and most turn out not to fit. The agent makes the first call: it checks the
buyer's requirements, shows what fits, answers the routine questions, and books a site visit
for the ones worth a salesperson's time. It must never promise what the developer has not
approved, and it must respect a buyer who does not want to be called.

## Why a fixed call flow, with an LLM variant beside it

The rule-based flow is predictable, cheap, fast enough for real-time voice, and can be tested
on thousands of calls. The LLM variant uses the same tools and guardrails, so the two can be
compared once it can be run on live calls. Keeping prices, availability and remediation in
tools (never in the model's memory) is what makes either one safe to put in front of
customers.

## Decisions

**Confirm what matters, not everything.** Reading back every answer makes a call tedious.
The agent reads back only values it understood approximately (a fuzzy locality match, a
garbled or implausible amount, a bare number) plus one summary of all four requirements
before searching, and the phone number by its last four digits. That costs about two extra turns
per call at 20% WER and is worth 29 points of task success.

**Plausibility as a signal of mishearing.** A 1 BHK for 9 crore is far more likely to be a
dropped "point" than a real request. Budgets outside the range quoted for that BHK in the
catalogue are treated as uncertain and read back.

**Use what the call already knows.** On an outbound call the dialled number is known, so the
agent offers it instead of asking for ten digits by voice.

**Brochure answers are retrieved, never written.** The question is matched to a brochure
topic and the stored fact is spoken. A missing fact gives a deferral to the sales team, and a
question with no clear topic gets "could you repeat your question?" once.

**Short replies.** Voice replies over 55 words are blocked; recommendations name at most two
projects, with price range and possession only.

**Guardrails as a separate last step.** Every reply is checked after it is composed, so the
checks hold for an LLM-written reply as much as a templated one.

## How the evaluation is kept honest

- Development and failure analysis used caller seeds 0 to 9,999. Reported numbers use seeds
  from 100,000, not looked at while the agent was being changed.
- A call is a success only if every check passes.
- Each robustness feature can be switched off, and its effect is measured, including the two
  that turned out not to matter in this simulator.

## Known gaps

- Callers read the agent's `expecting` field instead of its words.
- No barge-in (caller interrupting) or silence timing: both belong to the voice platform.
- Single language.
- The catalogue search treats "nearby" as a fixed list of neighbouring localities.

## Next steps

1. Connect a voice platform (speech-to-text, text-to-speech, telephony) to the existing API
   and record real transcripts.
2. Replay real transcripts through the evaluation instead of simulated callers.
3. Run the LLM agent on the same simulated calls and compare success, turns and cost.
4. Add Hindi and Kannada number words and code-mixed phrasings.
