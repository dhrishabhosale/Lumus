"""
Precomputes red-flag, Benford, graph, and milestone-mismatch signals for
every contract and writes them into signal_scores. This runs ONCE before
the orchestrator/demo, not live per query -- there's no reason to
recompute Benford's Law on every agent tool call, and doing it live
would make the demo slower and more failure-prone for no benefit.

priority_score = signal count weighted by contract value, per the
prioritization logic discussed in the architecture: a single flag on a
small contract should rank below multiple corroborating flags on a
large one.
"""

import sqlite3

from signals.red_flags import run_all as run_red_flags
from signals.benford import benford_by_vendor
from signals.graph_signals import build_graph, shell_cluster_candidates


def milestone_mismatch_flags(conn: sqlite3.Connection) -> dict[str, bool]:
    rows = conn.execute(
        "SELECT contract_id, evidence_date, claim_date FROM milestones"
    ).fetchall()
    flags: dict[str, bool] = {}
    for contract_id, evidence_date, claim_date in rows:
        mismatch = evidence_date > claim_date  # evidence recorded AFTER the claim
        flags[contract_id] = flags.get(contract_id, False) or mismatch
    return flags


def precompute(db_path: str = "data/argus.db"):
    conn = sqlite3.connect(db_path)

    red_flags = run_red_flags(conn)
    benford_results = benford_by_vendor(conn)
    g = build_graph(conn)
    clusters = shell_cluster_candidates(g)
    clustered_vendors = {v for c in clusters for v in c}
    mismatches = milestone_mismatch_flags(conn)

    contracts = conn.execute("SELECT contract_id, vendor_id, awarded_value FROM contracts").fetchall()

    conn.execute("DELETE FROM signal_scores")
    for contract_id, vendor_id, awarded_value in contracts:
        rf = red_flags.get(contract_id, {})
        single_bid = int(rf.get("single_bid", False))
        benford_flag = int(benford_results.get(vendor_id, {}).get("flag", False))
        graph_flag = int(vendor_id in clustered_vendors)
        milestone_flag = int(mismatches.get(contract_id, False))

        n_signals = single_bid + benford_flag + graph_flag + milestone_flag
        # normalize value into a 0-1-ish multiplier so a ₹40k contract with
        # 1 flag doesn't outrank a ₹40cr contract with 1 flag, but doesn't
        # let value alone dominate over corroborating signal count either
        value_weight = min(awarded_value / 10_000_000, 3.0)  # cap at 3x
        priority_score = n_signals * (1 + value_weight)

        conn.execute(
            "INSERT INTO signal_scores VALUES (?,?,?,?,?,?)",
            (contract_id, single_bid, benford_flag, graph_flag, milestone_flag, priority_score),
        )

    conn.commit()
    n_flagged = conn.execute(
        "SELECT COUNT(*) FROM signal_scores WHERE single_bid_flag=1 OR benford_flag=1 "
        "OR graph_cluster_flag=1 OR milestone_mismatch_flag=1"
    ).fetchone()[0]
    n_total = conn.execute("SELECT COUNT(*) FROM signal_scores").fetchone()[0]
    print(f"Precomputed signals for {n_total} contracts, {n_flagged} flagged on at least one signal")
    conn.close()


if __name__ == "__main__":
    precompute()
