"""
Red-flag indicators, per Fazekas & Kocsis's corruption-risk-indicator (CRI)
framework: proxy signals correlated with high-level corruption risk, not
proof of it. Each function returns a per-contract boolean/score -- these
stay as separate, traceable signals rather than being blended into one
opaque number (see signals/README notes in the main README).
"""

import sqlite3


def single_bid_flag(conn: sqlite3.Connection) -> dict[str, bool]:
    """Flags contracts awarded from a tender that received exactly one bid."""
    rows = conn.execute(
        """
        SELECT c.contract_id, t.single_bid
        FROM contracts c JOIN tenders t ON c.tender_id = t.tender_id
        """
    ).fetchall()
    return {contract_id: bool(single_bid) for contract_id, single_bid in rows}


def contract_share_concentration(conn: sqlite3.Connection, threshold: float = 0.15) -> dict[str, bool]:
    """Flags contracts belonging to a vendor whose share of total awarded
    value within their sector exceeds `threshold`. High concentration is
    one of Fazekas's core proxies for favoritism / winner pre-selection.
    """
    rows = conn.execute(
        "SELECT contract_id, vendor_id, sector, awarded_value FROM contracts"
    ).fetchall()

    sector_totals: dict[str, float] = {}
    vendor_sector_totals: dict[tuple[str, str], float] = {}
    for _, vendor_id, sector, value in rows:
        sector_totals[sector] = sector_totals.get(sector, 0) + value
        key = (vendor_id, sector)
        vendor_sector_totals[key] = vendor_sector_totals.get(key, 0) + value

    flags = {}
    for contract_id, vendor_id, sector, value in rows:
        share = vendor_sector_totals[(vendor_id, sector)] / sector_totals[sector]
        flags[contract_id] = share > threshold
    return flags


def award_value_inflation_flag(conn: sqlite3.Connection, threshold: float = 0.10) -> dict[str, bool]:
    """Flags contracts where the awarded value exceeds the tender's
    estimated value by more than `threshold` -- a proxy for post-tender
    value creep / scope inflation.
    """
    rows = conn.execute(
        """
        SELECT c.contract_id, c.awarded_value, t.estimated_value
        FROM contracts c JOIN tenders t ON c.tender_id = t.tender_id
        """
    ).fetchall()
    flags = {}
    for contract_id, awarded, estimated in rows:
        inflation = (awarded - estimated) / estimated if estimated else 0
        flags[contract_id] = inflation > threshold
    return flags


def run_all(conn: sqlite3.Connection) -> dict[str, dict]:
    """Combines all red-flag checks into a per-contract dict of individual
    boolean signals -- kept separate on purpose, see module docstring.
    """
    sb = single_bid_flag(conn)
    cc = contract_share_concentration(conn)
    vi = award_value_inflation_flag(conn)

    contract_ids = set(sb) | set(cc) | set(vi)
    return {
        cid: {
            "single_bid": sb.get(cid, False),
            "contract_share_concentration": cc.get(cid, False),
            "award_value_inflation": vi.get(cid, False),
        }
        for cid in contract_ids
    }


if __name__ == "__main__":
    conn = sqlite3.connect("data/argus.db")
    results = run_all(conn)
    flagged = {k: v for k, v in results.items() if any(v.values())}
    print(f"{len(flagged)} / {len(results)} contracts trip at least one red flag")
    for cid, flags in list(flagged.items())[:5]:
        print(cid, flags)
