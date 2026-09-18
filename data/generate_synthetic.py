"""
Generates a synthetic procurement dataset shaped like OCDS
(Open Contracting Data Standard): tender -> bid -> contract -> milestone
-> invoice -> payment, linked by a canonical vendor_id.

~950 unremarkable records + ~50 seeded anomalies across the four signal
families, so the demo can reliably pull a strong case from any detector:
  - single-bid clusters               (red flags)
  - a Benford-violating vendor         (statistical)
  - a 4-5 vendor shell/co-bidding ring (graph)
  - milestone-billed-before-evidence   (evidence reconciliation)

Output: SQLite database at data/argus.db
"""

import random
import sqlite3
import string
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from crypto.signing import generate_keypair

random.seed(42)

SECTORS = ["power", "roads", "water", "health", "education"]
N_VENDORS = 120
N_TENDERS = 300
N_SHELL_CLUSTER_VENDORS = 5
N_SINGLE_BID_ANOMALIES = 20
N_BENFORD_ANOMALY_INVOICES = 15
N_MILESTONE_MISMATCHES = 3


def rand_gstin(prefix=None):
    prefix = prefix or f"{random.randint(10,37)}"
    return prefix + "".join(random.choices(string.ascii_uppercase + string.digits, k=13))


def rand_pan():
    return "".join(random.choices(string.ascii_uppercase, k=5)) + \
           "".join(random.choices(string.digits, k=4)) + \
           random.choice(string.ascii_uppercase)


def rand_address():
    streets = ["MG Road", "Station Road", "Ring Road", "Industrial Area", "Civil Lines"]
    cities = ["Pune", "Nagpur", "Indore", "Bhopal", "Lucknow", "Patna", "Jaipur"]
    return f"{random.randint(1,200)} {random.choice(streets)}, {random.choice(cities)}"


def build_schema(conn):
    conn.executescript(
        """
        CREATE TABLE vendors (
            vendor_id TEXT PRIMARY KEY,
            name TEXT,
            gstin TEXT,
            pan TEXT,
            address TEXT,
            director_name TEXT,
            public_key TEXT
        );
        CREATE TABLE keys (
            entity_id TEXT PRIMARY KEY,
            entity_type TEXT,       -- 'vendor' | 'official'
            public_key TEXT,
            private_key TEXT        -- POC only: held centrally for demo purposes
        );
        CREATE TABLE tenders (
            tender_id TEXT PRIMARY KEY,
            sector TEXT,
            title TEXT,
            estimated_value REAL,
            single_bid INTEGER
        );
        CREATE TABLE bids (
            bid_id TEXT PRIMARY KEY,
            tender_id TEXT,
            vendor_id TEXT,
            bid_amount REAL,
            won INTEGER
        );
        CREATE TABLE contracts (
            contract_id TEXT PRIMARY KEY,
            tender_id TEXT,
            vendor_id TEXT,
            sector TEXT,
            awarded_value REAL
        );
        CREATE TABLE milestones (
            milestone_id TEXT PRIMARY KEY,
            contract_id TEXT,
            planned_pct REAL,
            claimed_pct REAL,
            claim_date TEXT,
            evidence_date TEXT
        );
        CREATE TABLE invoices (
            invoice_id TEXT PRIMARY KEY,
            contract_id TEXT,
            milestone_id TEXT,
            amount REAL
        );
        CREATE TABLE evidence (
            evidence_id TEXT PRIMARY KEY,
            milestone_id TEXT,
            evidence_hash TEXT,
            lat REAL,
            lon REAL,
            captured_at TEXT
        );
        CREATE TABLE signal_scores (
            contract_id TEXT PRIMARY KEY,
            single_bid_flag INTEGER,
            benford_flag INTEGER,
            graph_cluster_flag INTEGER,
            milestone_mismatch_flag INTEGER,
            priority_score REAL
        );
        CREATE TABLE ledger (
            idx INTEGER PRIMARY KEY,
            entry_type TEXT,
            contract_id TEXT,
            ref_id TEXT,
            payload TEXT,
            entry_hash TEXT,
            previous_hash TEXT,
            signature TEXT
        );
        CREATE TABLE investigation_cases (
            case_id TEXT PRIMARY KEY,
            contract_id TEXT,
            objective TEXT,
            trace TEXT,          -- JSON list of tool calls the orchestrator made
            disposition TEXT     -- 'open' | 'cleared' | 'escalated'
        );
        """
    )


def generate(db_path="data/argus.db"):
    if os.path.exists(db_path):
        os.remove(db_path)
    conn = sqlite3.connect(db_path)
    build_schema(conn)
    cur = conn.cursor()

    # --- Vendors ---
    shared_address = rand_address()
    shared_director = "R. Malhotra"
    shell_gstin_prefix = "27SHELL"

    vendor_ids = []
    for i in range(N_VENDORS):
        vendor_id = f"V-{i:04d}"
        vendor_ids.append(vendor_id)
        priv, pub = generate_keypair()

        if i < N_SHELL_CLUSTER_VENDORS:
            # Seeded shell/co-bidding ring: shared director + address + GSTIN prefix
            name = f"{random.choice(['Suvidha','Nirman','Progress','United','Apex'])} Infra {i}"
            gstin = shell_gstin_prefix + "".join(random.choices(string.digits, k=6))
            address = shared_address
            director = shared_director
        else:
            name = f"{random.choice(['Bharat','Ganga','Sunrise','Metro','Prime'])} Constructions {i}"
            gstin = rand_gstin()
            address = rand_address()
            director = f"Director {i}"

        cur.execute(
            "INSERT INTO vendors VALUES (?,?,?,?,?,?,?)",
            (vendor_id, name, gstin, rand_pan(), address, director, pub),
        )
        cur.execute(
            "INSERT INTO keys VALUES (?,?,?,?)",
            (vendor_id, "vendor", pub, priv),
        )

    # a couple of officials, for signing clearance decisions
    for oid in ["OFFICIAL-1", "OFFICIAL-2"]:
        priv, pub = generate_keypair()
        cur.execute("INSERT INTO keys VALUES (?,?,?,?)", (oid, "official", pub, priv))

    shell_vendors = vendor_ids[:N_SHELL_CLUSTER_VENDORS]
    normal_vendors = vendor_ids[N_SHELL_CLUSTER_VENDORS:]

    # --- Tenders, bids, contracts ---
    single_bid_tender_ids = set()
    contract_rows = []
    for t in range(N_TENDERS):
        tender_id = f"T-{t:04d}"
        sector = random.choice(SECTORS)
        estimated_value = round(random.uniform(500_000, 50_000_000), 2)

        force_single_bid = t < N_SINGLE_BID_ANOMALIES
        if force_single_bid:
            bidders = [random.choice(shell_vendors)]
            single_bid_tender_ids.add(tender_id)
        else:
            n_bidders = random.choice([1, 2, 3, 4]) if random.random() < 0.1 else random.randint(3, 6)
            if n_bidders == 1:
                single_bid_tender_ids.add(tender_id)
            # 40% chance a tender in the shell sector draws from the shell ring (co-bidding pattern)
            pool = shell_vendors if (t % 7 == 0) else normal_vendors
            bidders = random.sample(pool, min(n_bidders, len(pool)))

        cur.execute(
            "INSERT INTO tenders VALUES (?,?,?,?,?)",
            (tender_id, sector, f"{sector.title()} works package {t}", estimated_value,
             1 if tender_id in single_bid_tender_ids else 0),
        )

        winner = random.choice(bidders)
        for b, vendor_id in enumerate(bidders):
            bid_id = f"B-{t:04d}-{b}"
            bid_amount = round(estimated_value * random.uniform(0.85, 1.05), 2)
            cur.execute(
                "INSERT INTO bids VALUES (?,?,?,?,?)",
                (bid_id, tender_id, vendor_id, bid_amount, 1 if vendor_id == winner else 0),
            )

        contract_id = f"C-{t:04d}"
        awarded_value = round(estimated_value * random.uniform(0.9, 1.15), 2)
        cur.execute(
            "INSERT INTO contracts VALUES (?,?,?,?,?)",
            (contract_id, tender_id, winner, sector, awarded_value),
        )
        contract_rows.append((contract_id, winner, sector, awarded_value))

    # --- Milestones, invoices, evidence ---
    mismatch_budget = N_MILESTONE_MISMATCHES
    for idx, (contract_id, vendor_id, sector, awarded_value) in enumerate(contract_rows):
        n_milestones = 3
        for m in range(n_milestones):
            milestone_id = f"{contract_id}-M{m}"
            planned_pct = (m + 1) * 100 / n_milestones
            force_mismatch = mismatch_budget > 0 and idx % 97 == 0
            if force_mismatch:
                claimed_pct = planned_pct  # billed as fully done
                claim_date = "2025-06-01"
                evidence_date = "2025-06-15"  # evidence recorded AFTER the claim -> mismatch
                mismatch_budget -= 1
            else:
                claimed_pct = planned_pct
                claim_date = "2025-06-01"
                evidence_date = "2025-05-28"  # evidence precedes claim -> normal

            cur.execute(
                "INSERT INTO milestones VALUES (?,?,?,?,?,?)",
                (milestone_id, contract_id, planned_pct, claimed_pct, claim_date, evidence_date),
            )

            invoice_amount = round(awarded_value / n_milestones, 2)
            # Seed Benford anomaly: shell vendors' invoice amounts are round/manufactured numbers
            if vendor_id in shell_vendors and idx < N_BENFORD_ANOMALY_INVOICES:
                invoice_amount = round(random.choice([500000, 1000000, 1500000, 2000000]), 2)

            invoice_id = f"{milestone_id}-INV"
            cur.execute(
                "INSERT INTO invoices VALUES (?,?,?,?)",
                (invoice_id, contract_id, milestone_id, invoice_amount),
            )

            evidence_id = f"{milestone_id}-EV"
            lat, lon = round(random.uniform(8.0, 28.0), 4), round(random.uniform(72.0, 88.0), 4)
            cur.execute(
                "INSERT INTO evidence VALUES (?,?,?,?,?,?)",
                (evidence_id, milestone_id, f"sha256:{random.getrandbits(64):x}", lat, lon, evidence_date),
            )

    conn.commit()
    conn.close()
    print(f"Generated synthetic dataset at {db_path}")
    print(f"  vendors: {N_VENDORS} ({N_SHELL_CLUSTER_VENDORS} in seeded shell cluster)")
    print(f"  tenders: {N_TENDERS} ({len(single_bid_tender_ids)} single-bid)")
    print(f"  seeded milestone mismatches: {N_MILESTONE_MISMATCHES}")


if __name__ == "__main__":
    generate()
