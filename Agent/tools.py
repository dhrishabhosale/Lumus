"""
The six tools the orchestrator agent is allowed to call. All read-only
queries against precomputed/structured data -- nothing here does
expensive computation live, so the agent stays fast and the demo stays
reliable. Signal scores (red flags, Benford, graph clustering) are
computed as a batch step (see demo.py) BEFORE the agent runs; these
tools just read that output plus the underlying records.
"""

import json
import sqlite3

import networkx as nx

from signals.graph_signals import build_graph, vendor_relationship_summary


def _row_to_dict(cursor, row):
    return {desc[0]: value for desc, value in zip(cursor.description, row)}


def search_contracts(conn: sqlite3.Connection, sector: str = None, flagged_only: bool = True) -> list[dict]:
    """Find contracts, optionally filtered by sector and/or flagged status."""
    query = """
        SELECT c.contract_id, c.sector, c.vendor_id, c.awarded_value,
               s.single_bid_flag, s.benford_flag, s.graph_cluster_flag,
               s.milestone_mismatch_flag, s.priority_score
        FROM contracts c
        LEFT JOIN signal_scores s ON c.contract_id = s.contract_id
        WHERE 1=1
    """
    params = []
    if sector:
        query += " AND c.sector = ?"
        params.append(sector)
    if flagged_only:
        query += " AND (s.single_bid_flag=1 OR s.benford_flag=1 OR s.graph_cluster_flag=1 OR s.milestone_mismatch_flag=1)"
    query += " ORDER BY s.priority_score DESC LIMIT 25"

    cur = conn.execute(query, params)
    return [_row_to_dict(cur, r) for r in cur.fetchall()]


def get_vendor_history(conn: sqlite3.Connection, vendor_id: str) -> dict:
    """Contract and bid history for one vendor -- win rate, total value,
    number of single-bid wins.
    """
    contracts = conn.execute(
        "SELECT contract_id, sector, awarded_value FROM contracts WHERE vendor_id=?",
        (vendor_id,),
    ).fetchall()
    bids = conn.execute(
        "SELECT tender_id, won FROM bids WHERE vendor_id=?", (vendor_id,)
    ).fetchall()

    n_bids = len(bids)
    n_wins = sum(1 for _, won in bids if won)

    return {
        "vendor_id": vendor_id,
        "total_contracts_won": len(contracts),
        "total_awarded_value": sum(c[2] for c in contracts),
        "sectors": sorted(set(c[1] for c in contracts)),
        "bids_placed": n_bids,
        "win_rate": round(n_wins / n_bids, 3) if n_bids else None,
    }


def query_vendor_graph(conn: sqlite3.Connection, vendor_id: str) -> dict:
    """Co-bidders and shell-cluster membership for one vendor."""
    g = build_graph(conn)
    return vendor_relationship_summary(g, vendor_id)


def get_registration_info(conn: sqlite3.Connection, vendor_id: str) -> dict:
    """Raw registration attributes -- what the graph's SHARES_* edges were built from."""
    row = conn.execute(
        "SELECT vendor_id, name, gstin, pan, address, director_name FROM vendors WHERE vendor_id=?",
        (vendor_id,),
    ).fetchone()
    if not row:
        return {"error": "vendor not found"}
    return {
        "vendor_id": row[0], "name": row[1], "gstin": row[2],
        "pan": row[3], "address": row[4], "director_name": row[5],
    }


def get_evidence(conn: sqlite3.Connection, milestone_id: str) -> dict:
    """Evidence record for a milestone claim -- geotag, timestamp, hash."""
    row = conn.execute(
        "SELECT evidence_id, evidence_hash, lat, lon, captured_at FROM evidence WHERE milestone_id=?",
        (milestone_id,),
    ).fetchone()
    milestone = conn.execute(
        "SELECT planned_pct, claimed_pct, claim_date, evidence_date FROM milestones WHERE milestone_id=?",
        (milestone_id,),
    ).fetchone()
    if not row or not milestone:
        return {"error": "milestone or evidence not found"}
    return {
        "milestone_id": milestone_id,
        "evidence_hash": row[1], "lat": row[2], "lon": row[3], "captured_at": row[4],
        "planned_pct": milestone[0], "claimed_pct": milestone[1],
        "claim_date": milestone[2], "evidence_date": milestone[3],
        "evidence_predates_claim": milestone[3] <= milestone[2],
    }


def get_document_excerpt(conn: sqlite3.Connection, contract_id: str, page: int = None) -> dict:
    """Provenance pointer back to source tender document.

    POC stub: real implementation would look up the page/line extracted
    by the docx/pdf pipeline (see data/generate_synthetic.py note) and
    return the actual text span + confidence score. Here it returns a
    placeholder that demonstrates the shape of what the extraction
    pipeline would provide.
    """
    tender = conn.execute(
        "SELECT tender_id, title, sector, estimated_value FROM tenders "
        "WHERE tender_id = (SELECT tender_id FROM contracts WHERE contract_id=?)",
        (contract_id,),
    ).fetchone()
    if not tender:
        return {"error": "contract not found"}
    return {
        "contract_id": contract_id,
        "tender_id": tender[0],
        "source_document": f"{tender[0]}.pdf",
        "page": page or "unresolved -- pipeline extraction not run on this synthetic record",
        "excerpt": f"[synthetic placeholder] {tender[1]}, estimated value {tender[3]}",
        "confidence": 0.0,
        "note": "Real deployment uses signals/../pdf extraction pipeline for live documents; "
                "this synthetic dataset has no underlying PDF to point to.",
    }


# Tool schemas for Claude's tool-use API
TOOL_SCHEMAS = [
    {
        "name": "search_contracts",
        "description": "Find contracts, optionally filtered by sector, ranked by priority score.",
        "input_schema": {
            "type": "object",
            "properties": {
                "sector": {"type": "string", "description": "e.g. power, roads, water, health, education"},
                "flagged_only": {"type": "boolean", "description": "restrict to contracts with at least one signal flag"},
            },
        },
    },
    {
        "name": "get_vendor_history",
        "description": "Contract/bid history for one vendor: win rate, total value, sectors.",
        "input_schema": {
            "type": "object",
            "properties": {"vendor_id": {"type": "string"}},
            "required": ["vendor_id"],
        },
    },
    {
        "name": "query_vendor_graph",
        "description": "Co-bidders and shell-cluster membership for one vendor.",
        "input_schema": {
            "type": "object",
            "properties": {"vendor_id": {"type": "string"}},
            "required": ["vendor_id"],
        },
    },
    {
        "name": "get_registration_info",
        "description": "Raw registration attributes (GSTIN, PAN, address, director) for one vendor.",
        "input_schema": {
            "type": "object",
            "properties": {"vendor_id": {"type": "string"}},
            "required": ["vendor_id"],
        },
    },
    {
        "name": "get_evidence",
        "description": "Evidence record for a milestone claim: geotag, timestamp, whether evidence predates the claim.",
        "input_schema": {
            "type": "object",
            "properties": {"milestone_id": {"type": "string"}},
            "required": ["milestone_id"],
        },
    },
    {
        "name": "get_document_excerpt",
        "description": "Provenance pointer back to the source tender document for a contract.",
        "input_schema": {
            "type": "object",
            "properties": {
                "contract_id": {"type": "string"},
                "page": {"type": "integer"},
            },
            "required": ["contract_id"],
        },
    },
]

DISPATCH = {
    "search_contracts": search_contracts,
    "get_vendor_history": get_vendor_history,
    "query_vendor_graph": query_vendor_graph,
    "get_registration_info": get_registration_info,
    "get_evidence": get_evidence,
    "get_document_excerpt": get_document_excerpt,
}


def call_tool(conn: sqlite3.Connection, name: str, tool_input: dict):
    fn = DISPATCH.get(name)
    if fn is None:
        return {"error": f"unknown tool {name}"}
    try:
        return fn(conn, **tool_input)
    except Exception as e:
        return {"error": str(e)}
