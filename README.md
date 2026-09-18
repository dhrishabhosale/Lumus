# LUmus — Procurement Integrity Platform 

Investigator-facing procurement anomaly detection. Surfaces patterns worth
human review with a full, tamper-evident evidence trail — it never labels
a vendor as corrupt.

## What's real vs. precomputed

| Component | Status |
|---|---|
| Synthetic data generation | Real — `data/generate_synthetic.py` |
| Entity resolution | Real — GSTIN/PAN exact match + shared-attribute clustering |
| 4 signal detectors | Real, run as a batch step (`signals/precompute.py`) before the agent runs |
| Graph querying | Real — networkx, live queries via `agent/tools.py` |
| LLM orchestrator | Real — live Claude tool-calling loop |
| 6 agent tools | Real — all query real tables/graph |
| SHA-256 ledger | Real — `crypto/ledger.py` |
| Ed25519 signing | Real — `crypto/signing.py` |
| Tamper verification | Real — see the tamper demo in `demo.py` step 6 |
| Investigator queue UI | Not in this repo yet — this is the backend/logic layer; Streamlit UI is the next piece to build on top |
| PDF extraction pipeline | Not in this repo — separate, scoped demo on 2-3 real sample tenders, decoupled from this synthetic dataset |

Signal scores are the one thing intentionally precomputed rather than
recalculated live — there's no reason to re-run a chi-square test on every
agent tool call. Everything else in the table above runs for real.

## Structure

```
crypto/
  ledger.py       hash-chained append-only ledger (tamper-evidence)
  signing.py      Ed25519 sign/verify (non-repudiation)
data/
  generate_synthetic.py   OCDS-shaped dataset generator with seeded anomalies
signals/
  red_flags.py     Fazekas-style CRI indicators (single-bid, concentration, value inflation)
  benford.py        Benford's Law chi-square test on invoice amounts
  graph_signals.py  networkx vendor relationship graph, shell-cluster detection
  precompute.py     batches all four signal families into signal_scores table
agent/
  tools.py          the six read-only tools the orchestrator can call
  orchestrator.py   Claude tool-calling loop — the one genuinely agentic component
demo.py             ties it all together end-to-end
```

## Setup

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-...   # optional — demo runs without it, skips live agent step
python demo.py
```

## What each layer maps to in the architecture

- **Data layer** → `data/generate_synthetic.py` (synthetic here; real deployment
  swaps this for the extraction pipeline over actual tender/bid/payment records)
- **Cryptographic layer** → `crypto/`
- **Signal layer** → `signals/`
- **Orchestrator** → `agent/` — the only component that branches on intermediate
  results rather than running a fixed sequence, which is what makes it agentic
  rather than a parameterized pipeline
- **Investigator queue** → `investigation_cases` table in the schema; UI not yet built

## Design choices worth knowing before extending this

- **No single credibility/trust score anywhere.** Every signal (single-bid,
  Benford, graph cluster, milestone mismatch) stays a separate boolean in
  `signal_scores` — never blended into one opaque number. This is deliberate:
  a blended score is a de facto corruption label, which the system is
  explicitly designed not to produce.
- **The hash chain proves integrity, not truth.** `verify_chain()` proves a
  record wasn't altered after creation. It says nothing about whether the
  record was accurate when it was created — that's the signal layer and a
  human investigator's job.
- **Not a real blockchain, on purpose.** One trusted writer (this system),
  not multiple mutually-distrusting parties — a single-node hash chain gets
  the same tamper-evidence guarantee without consensus-layer overhead.
- **`priority_score` weights signal count by contract value**, so a single
  flag on a small contract doesn't outrank three corroborating flags on a
  large one. See `signals/precompute.py` for the exact formula.
