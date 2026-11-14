"""Puanlama profilleri ve donmuş sürümleri.

Bir sürüm, yayımlandığı andaki güncel kuralların kopyasıdır; sonradan öğrenilen bilgi onu
değiştirmez (veritabanı tetikleyicisi). Doğrulanmamış kural ya da doğrulanmamış kritik
gereksinim varsa sürüm `provisional` (geçici) işaretlenir.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from lai.core.rules import CATALOG_VERSION

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
        inventory_version = self.s.conn.execute(
            "SELECT id FROM inventory_versions WHERE inventory_id=? ORDER BY version DESC LIMIT 1",
            (inventory_id,)).fetchone()
        dimensions = [d["code"] for d in self.s.inventory.dimensions(inventory_id)]
        if not dimensions:
            first_ids = dict(self.s.conn.execute(
                "SELECT entry_key,MIN(id) FROM knowledge_entries WHERE inventory_id=? "
                "AND kind='rule' AND rule_type='dimension_def' GROUP BY entry_key", (inventory_id,)))
            dimensions = [e.content["body"]["dimension"] for e in
                          sorted(rules, key=lambda e:first_ids.get(e.entry_key,e.id))
                          if e.rule_type == "dimension_def"]
        requirements = []
        for r in self.s.questions.matrix(inventory_id):
            dims = [r.dimension_code or "*"]
            if r.req_key.startswith("m6:"):
                row = self.s.conn.execute("SELECT payload FROM learning_gaps WHERE inventory_id=? AND gap_key=?",
                                          (inventory_id, r.req_key[3:])).fetchone()
                if row:
                    dims = json.loads(row[0])["dimensions"]
            requirements.append({"req_key":r.req_key, "dimensions":dims, "critical":r.critical,
                                 "status":r.status, "category":r.category, "reason":r.status_reason})
        fit = self.s.conn.execute("SELECT MAX(id) FROM fit_runs WHERE inventory_id=?", (inventory_id,)).fetchone()[0]
        reviews = [{"dimension_code":r[0], "detail":json.loads(r[1])} for r in self.s.conn.execute(
            "SELECT dimension_code,detail FROM dimension_reviews WHERE fit_run_id=? ORDER BY dimension_code", (fit,))]
        snap = {
            "snapshot_format": 2,
            "catalog_version": CATALOG_VERSION,
            "inventory_id": inventory_id,
            "inventory_version_id": inventory_version[0] if inventory_version else None,
            "dimension_order": dimensions,
            "requirements": requirements,
            "dimension_reviews": reviews,
            "rules": [{"entry_id": e.id, "entry_key": e.entry_key, "version": e.version,
                       "rule_type": e.rule_type, "content": e.content, "summary": e.summary,
                       "item_number": e.item_number, "dimension_code": e.dimension_code,
                       "confidence": e.confidence, "source": e.source,
                       "validation": e.validation, "confirmation_ref": e.confirmation_ref,
                       "evidence_ids": e.evidence_ids} for e in rules],
            "unconfirmed_critical": sorted(unconfirmed_critical),
            "learning_facts": [{"entry_id":e.id,"entry_key":e.entry_key,"version":e.version,
                                "content":e.content,"confidence":e.confidence}
                               for e in self.s.knowledge.current(inventory_id, kind="fact")
                               if e.validation != "rejected"],
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
            provisional = (not snap["rules"] or bool(snap["unconfirmed_critical"]) or bool(snap["dimension_reviews"])
                           or any(r["confidence"] != Confidence.CONFIRMED for r in snap["rules"]))
            vid = c.execute("INSERT INTO scoring_profile_versions(profile_id, version, snapshot, "
                            "content_hash, provisional, note) VALUES (?,?,?,?,?,?)",
                            (profile_id, (last.version if last else 0) + 1, _canonical(snap),
                             digest, int(provisional), note)).lastrowid
            c.executemany("INSERT INTO profile_version_entries VALUES (?,?)",
                          [(vid, e) for e in entry_ids])
        return self.get_version(vid)
