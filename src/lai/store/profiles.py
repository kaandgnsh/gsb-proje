"""Puanlama profilleri ve donmuş sürümleri.

Bir sürüm, yayımlandığı andaki güncel kuralların kopyasıdır; sonradan öğrenilen bilgi onu
değiştirmez (veritabanı tetikleyicisi). Doğrulanmamış kural ya da doğrulanmamış kritik
gereksinim varsa sürüm `provisional` (geçici) işaretlenir.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from .enums import Confidence


@dataclass
class ProfileVersion:
    id: int
    profile_id: int
    version: int
    snapshot: dict
    content_hash: str
    provisional: bool
    note: str | None
    created_at: str


def _canonical(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class ProfileRepo:
    def __init__(self, store):
        self.s = store

    def ensure_profile(self, inventory_id: int, name: str = "Varsayılan") -> int:
        with self.s.transaction() as c:
            row = c.execute("SELECT id FROM scoring_profiles WHERE inventory_id=? AND name=?",
                            (inventory_id, name)).fetchone()
            if row:
                return row[0]
            return c.execute("INSERT INTO scoring_profiles(inventory_id, name) VALUES (?,?)",
                             (inventory_id, name)).lastrowid

    @staticmethod
    def _pv(row) -> ProfileVersion:
        d = dict(row)
        d["snapshot"] = json.loads(d["snapshot"])
        d["provisional"] = bool(d["provisional"])
        return ProfileVersion(**d)

    def get_version(self, version_id: int) -> ProfileVersion:
        row = self.s.conn.execute("SELECT * FROM scoring_profile_versions WHERE id=?",
                                  (version_id,)).fetchone()
        if row is None:
            raise KeyError(version_id)
        return self._pv(row)

    def versions(self, profile_id: int) -> list[ProfileVersion]:
        return [self._pv(r) for r in self.s.conn.execute(
            "SELECT * FROM scoring_profile_versions WHERE profile_id=? ORDER BY version",
            (profile_id,))]

    def latest_version(self, profile_id: int) -> ProfileVersion | None:
        v = self.versions(profile_id)
        return v[-1] if v else None

    def _snapshot(self, inventory_id: int) -> tuple[dict, list[int]]:
        rules = [e for e in self.s.knowledge.current(inventory_id, kind="rule")
                 if e.validation != "rejected"]
        unconfirmed_critical = [r.req_key for r in self.s.questions.matrix(inventory_id)
                                if r.critical and r.status != Confidence.CONFIRMED]
        snap = {
            "rules": [{"entry_id": e.id, "entry_key": e.entry_key, "version": e.version,
                       "rule_type": e.rule_type, "content": e.content, "summary": e.summary,
                       "item_number": e.item_number, "dimension_code": e.dimension_code,
                       "confidence": e.confidence, "source": e.source} for e in rules],
            "unconfirmed_critical": sorted(unconfirmed_critical),
        }
        return snap, [e.id for e in rules]

    def publish_version(self, profile_id: int, *, note: str | None = None) -> ProfileVersion:
        inv = self.s.conn.execute("SELECT inventory_id FROM scoring_profiles WHERE id=?",
                                  (profile_id,)).fetchone()
        if inv is None:
            raise KeyError(f"profil yok: {profile_id}")
        with self.s.transaction() as c:
            snap, entry_ids = self._snapshot(inv[0])
            digest = hashlib.sha256(_canonical(snap).encode()).hexdigest()
            last = self.latest_version(profile_id)
            if last and last.content_hash == digest:
                return last  # değişiklik yok: yeni sürüm üretilmez
            provisional = (not snap["rules"] or bool(snap["unconfirmed_critical"])
                           or any(r["confidence"] != Confidence.CONFIRMED for r in snap["rules"]))
            vid = c.execute("INSERT INTO scoring_profile_versions(profile_id, version, snapshot, "
                            "content_hash, provisional, note) VALUES (?,?,?,?,?,?)",
                            (profile_id, (last.version if last else 0) + 1, _canonical(snap),
                             digest, int(provisional), note)).lastrowid
            c.executemany("INSERT INTO profile_version_entries VALUES (?,?)",
                          [(vid, e) for e in entry_ids])
        return self.get_version(vid)
