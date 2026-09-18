"""
End-to-end POC demo. Run this to walk the full flow discussed in the
architecture:

  1. generate synthetic dataset (if not already present)
  2. precompute signal scores (red flags, Benford, graph, milestones)
  3. run the orchestrator agent on an objective -- live Claude tool-calling
  4. take the top case, write its verification decision to the ledger, sign it
  5. verify the chain is intact
  6. tamper with a past entry and show verify_chain() catching it live
     (this is the "wow" beat -- run this last, in front of judges)

Usage:
    export ANTHROPIC_API_KEY=...
    python demo.py
"""

import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data.generate_synthetic import generate
from signals.precompute import precompute
from crypto.ledger import Ledger
from crypto.signing import sign_payload, verify_signature

DB_PATH = "data/argus.db"


def step(title):
    print(f"\n{'='*60}\n{title}\n{'='*60}")


def main():
    if not os.path.exists(DB_PATH):
        step("1. Generating synthetic dataset")
        generate(DB_PATH)
    else:
        print(f"Using existing dataset at {DB_PATH} (delete it to regenerate)")

    step("2. Precomputing signal scores (red flags, Benford, graph, milestones)")
    precompute(DB_PATH)

    step("3. Running orchestrator agent")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY not set -- skipping live agent call.")
        print("Set it and re-run to see the orchestrator branch through tool calls.")
        case_trace = []
        top_contract_id = sqlite3.connect(DB_PATH).execute(
            "SELECT contract_id FROM signal_scores ORDER BY priority_score DESC LIMIT 1"
        ).fetchone()[0]
    else:
        from agent.orchestrator import investigate
        result = investigate("Investigate power-sector contracts for anomalies", DB_PATH)
        print("\nCase summary:\n", result["summary"])
        print(f"\nTool calls made ({len(result['trace'])}):")
        for t in result["trace"]:
            print(f"  - {t['tool']}({t['input']})")
        case_trace = result["trace"]
        conn = sqlite3.connect(DB_PATH)
        top_contract_id = conn.execute(
            "SELECT contract_id FROM signal_scores ORDER BY priority_score DESC LIMIT 1"
        ).fetchone()[0]

    step(f"4. Recording investigator disposition for {top_contract_id} to the ledger")
    conn = sqlite3.connect(DB_PATH)
    priv, pub = conn.execute(
        "SELECT private_key, public_key FROM keys WHERE entity_id='OFFICIAL-1'"
    ).fetchone()

    ledger = Ledger(path="data/demo_ledger.jsonl")
    disposition_payload = {
        "contract_id": top_contract_id,
        "decision": "escalated",
        "reason": "multiple corroborating signals",
        "trace_summary": [t["tool"] for t in case_trace],
    }
    signature = sign_payload(priv, disposition_payload)
    entry = ledger.append(
        entry_type="disposition",
        contract_id=top_contract_id,
        ref_id="OFFICIAL-1",
        payload=disposition_payload,
    )
    entry.signature = signature
    entry.signer_public_key = pub
    print(f"Ledger entry {entry.index} written, hash {entry.entry_hash[:16]}...")
    print(f"Signature valid: {verify_signature(pub, disposition_payload, signature)}")

    step("5. Verifying chain integrity")
    valid, break_index = ledger.verify_chain()
    print(f"Chain valid: {valid}")

    step("6. TAMPER DEMO: silently editing a past ledger entry")
    ledger.tamper_with(0, {"decision": "cleared", "reason": "tampered by demo"})
    valid, break_index = ledger.verify_chain()
    print(f"Chain valid after tamper: {valid}")
    print(f"Break detected at entry index: {break_index}")
    print("\nThis is the core claim: not that the system can't be lied to,")
    print("but that if a past record IS altered, it's provably, immediately detectable.")


if __name__ == "__main__":
    main()
