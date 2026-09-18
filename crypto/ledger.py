"""
Hash-chained, append-only ledger.

Guarantees TAMPER-EVIDENCE only: proves an entry was not silently altered
after it was written. It does NOT prove the entry was true when it was
written -- that's the job of the signal layer and human investigators.

Each entry's hash is a function of its own data plus the previous entry's
hash, so editing any past entry breaks every hash after it. This is the
same block-linking primitive blockchains use, without the distributed
consensus layer -- appropriate here because there is exactly one trusted
writer (this system), not multiple mutually-distrusting parties.
"""

import hashlib
import json
import time
from dataclasses import dataclass, field, asdict
from typing import Optional


GENESIS_HASH = "0" * 64


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hash_evidence_file(file_bytes: bytes) -> str:
    """Hash the evidence file itself (photo, receipt PDF, etc).

    Storing this hash alongside the ledger entry closes the gap where
    someone swaps the underlying evidence file without touching the
    ledger row that references it.
    """
    return sha256_hex(file_bytes)


@dataclass
class LedgerEntry:
    index: int
    entry_type: str          # "milestone_claim" | "invoice" | "verification" | "disposition"
    contract_id: str
    ref_id: str               # milestone_id / invoice_id / case_id, depending on entry_type
    payload: dict              # the actual claim/decision data
    evidence_hash: Optional[str]
    timestamp: float
    previous_hash: str
    entry_hash: str = field(default="")
    signature: Optional[str] = None   # hex signature, set separately by signing.py
    signer_public_key: Optional[str] = None

    def compute_hash(self) -> str:
        canonical = json.dumps(
            {
                "index": self.index,
                "entry_type": self.entry_type,
                "contract_id": self.contract_id,
                "ref_id": self.ref_id,
                "payload": self.payload,
                "evidence_hash": self.evidence_hash,
                "timestamp": self.timestamp,
                "previous_hash": self.previous_hash,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return sha256_hex(canonical)

    def to_dict(self) -> dict:
        return asdict(self)


class Ledger:
    """In-memory hash chain with JSONL persistence.

    For the POC this is a flat file; in production this table would live
    in the same database as everything else, with entry_hash indexed.
    """

    def __init__(self, path: str = "ledger.jsonl"):
        self.path = path
        self.entries: list[LedgerEntry] = []
        self._load()

    def _load(self):
        try:
            with open(self.path, "r") as f:
                for line in f:
                    if line.strip():
                        d = json.loads(line)
                        self.entries.append(LedgerEntry(**d))
        except FileNotFoundError:
            pass

    def _persist_append(self, entry: LedgerEntry):
        with open(self.path, "a") as f:
            f.write(json.dumps(entry.to_dict()) + "\n")

    def append(
        self,
        entry_type: str,
        contract_id: str,
        ref_id: str,
        payload: dict,
        evidence_hash: Optional[str] = None,
    ) -> LedgerEntry:
        previous_hash = self.entries[-1].entry_hash if self.entries else GENESIS_HASH
        entry = LedgerEntry(
            index=len(self.entries),
            entry_type=entry_type,
            contract_id=contract_id,
            ref_id=ref_id,
            payload=payload,
            evidence_hash=evidence_hash,
            timestamp=time.time(),
            previous_hash=previous_hash,
        )
        entry.entry_hash = entry.compute_hash()
        self.entries.append(entry)
        self._persist_append(entry)
        return entry

    def verify_chain(self) -> tuple[bool, Optional[int]]:
        """Walk the chain and recompute every hash.

        Returns (is_valid, index_of_first_break). index_of_first_break is
        None if the chain is intact.
        """
        expected_previous = GENESIS_HASH
        for entry in self.entries:
            if entry.previous_hash != expected_previous:
                return False, entry.index
            recomputed = entry.compute_hash()
            if recomputed != entry.entry_hash:
                return False, entry.index
            expected_previous = entry.entry_hash
        return True, None

    def tamper_with(self, index: int, new_payload: dict):
        """FOR DEMO PURPOSES ONLY.

        Simulates an insider silently editing a past ledger row directly
        in storage (bypassing append-only discipline), so verify_chain()
        can be shown catching it live.
        """
        if index >= len(self.entries):
            raise IndexError("no such entry")
        self.entries[index].payload = new_payload
        # entry_hash is deliberately NOT recomputed here -- that's the point.
        # A real attacker with DB write access wouldn't recompute it either
        # unless they also rewrote every subsequent entry, which this class
        # doesn't help them do.


if __name__ == "__main__":
    ledger = Ledger(path="/tmp/demo_ledger.jsonl")
    ledger.append(
        entry_type="milestone_claim",
        contract_id="C-1001",
        ref_id="M-3",
        payload={"claimed_pct_complete": 60, "claimed_amount": 450000},
        evidence_hash=hash_evidence_file(b"fake photo bytes"),
    )
    ledger.append(
        entry_type="verification",
        contract_id="C-1001",
        ref_id="M-3",
        payload={"result": "matched", "checked_by": "signal_layer"},
    )
    valid, break_index = ledger.verify_chain()
    print(f"chain valid: {valid}")

    ledger.tamper_with(0, {"claimed_pct_complete": 95, "claimed_amount": 900000})
    valid, break_index = ledger.verify_chain()
    print(f"chain valid after tamper: {valid}, broke at index: {break_index}")
