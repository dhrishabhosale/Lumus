"""
Benford's Law leading-digit test.

Naturally occurring amounts (invoices, bids) tend to follow Benford's
distribution: digit 1 leads ~30% of the time, digit 9 leads ~4.6% of the
time -- not uniform. Fabricated or manually manufactured numbers (round
figures, evenly distributed digits) tend to deviate from this, which is
what the seeded shell-vendor invoices in generate_synthetic.py do on
purpose.

This is a cheap, independent signal that doesn't rely on the red-flag
framework at all -- useful precisely because it can catch a case the
red flags miss, and vice versa.
"""

import sqlite3
from collections import Counter
from scipy import stats

BENFORD_EXPECTED = {d: __import__("math").log10(1 + 1 / d) for d in range(1, 10)}


def leading_digit(amount: float) -> int:
    s = str(int(abs(amount)))
    s = s.lstrip("0")
    return int(s[0]) if s else 1


def benford_test(amounts: list[float]) -> dict:
    """Chi-square goodness-of-fit test against Benford's expected
    leading-digit distribution. Returns observed/expected frequencies,
    the chi-square statistic, p-value, and a boolean flag.
    """
    if len(amounts) < 10:
        return {"flag": False, "reason": "insufficient sample size", "n": len(amounts)}

    digits = [leading_digit(a) for a in amounts if a > 0]
    counts = Counter(digits)
    n = len(digits)

    observed = [counts.get(d, 0) for d in range(1, 10)]
    expected = [BENFORD_EXPECTED[d] * n for d in range(1, 10)]

    chi2, p_value = stats.chisquare(f_obs=observed, f_exp=expected)

    return {
        "flag": bool(p_value < 0.05),
        "chi2": round(chi2, 3),
        "p_value": round(p_value, 5),
        "n": n,
        "observed_pct": {d: round(observed[d - 1] / n, 3) for d in range(1, 10)},
        "expected_pct": {d: round(BENFORD_EXPECTED[d], 3) for d in range(1, 10)},
    }


def benford_by_vendor(conn: sqlite3.Connection) -> dict[str, dict]:
    """Runs the Benford test per vendor across all their invoice amounts.
    Flags a vendor (and by extension their contracts) whose invoicing
    pattern deviates significantly from the expected distribution.
    """
    rows = conn.execute(
        """
        SELECT c.vendor_id, i.amount
        FROM invoices i JOIN contracts c ON i.contract_id = c.contract_id
        """
    ).fetchall()

    by_vendor: dict[str, list[float]] = {}
    for vendor_id, amount in rows:
        by_vendor.setdefault(vendor_id, []).append(amount)

    return {vendor_id: benford_test(amounts) for vendor_id, amounts in by_vendor.items()}


if __name__ == "__main__":
    conn = sqlite3.connect("data/argus.db")
    results = benford_by_vendor(conn)
    flagged = {v: r for v, r in results.items() if r.get("flag")}
    print(f"{len(flagged)} / {len(results)} vendors flagged by Benford's Law")
    for vendor_id, r in list(flagged.items())[:5]:
        print(vendor_id, "p =", r["p_value"], "n =", r["n"])
