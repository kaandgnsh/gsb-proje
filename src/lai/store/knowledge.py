"""Kalıcı bilgi kayıtları: salt eklenir, sürümlü, kanıtlı.

Kural: belirsiz bilgi kendiliğinden doğrulanmış sayılmaz. CONFIRMED yalnız
`confirm()` (belge ya da kullanıcı kaynağı + onay referansı) veya
`apply_user_correction()` ile oluşur; veritabanı da aynı kuralı CHECK ile zorlar.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from .enums import TRUSTED_SOURCES, Confidence, Source, Validation


@dataclass
class Entry:
    id: int
    inventory_id: int
    entry_key: str
    version: int
    supersedes_id: int | None
    kind: str
    rule_type: str | None
    content: dict
    summary: str
    item_number: int | None
    dimension_code: str | None
    confidence: str
    source: str
    validation: str
    confirmation_ref: str | None
    reason: str | None
    created_by: str
    created_at: str
    evidence_ids: list[int] = field(default_factory=list)


@dataclass
class Correction:
    id: int
    inventory_id: int
    target_entry_id: int | None
    resulting_entry_id: int
    evidence_id: int
    explanation: str
    user_text: str | None
    created_at: str


_COLS = ("inventory_id, entry_key, version, supersedes_id, kind, rule_type, content, summary, "
         "item_number, dimension_code, confidence, source, validation, confirmation_ref, reason, "
         "created_by")


class KnowledgeRepo:
    def __init__(self, store):
        self.s = store

    # -- okuma ----------------------------------------------------------------------------
    def _entry(self, row) -> Entry:
        d = dict(row)
        d["content"] = json.loads(d["content"])
        d["evidence_ids"] = [r[0] for r in self.s.conn.execute(
            "SELECT evidence_id FROM knowledge_evidence WHERE entry_id=? ORDER BY evidence_id",
            (d["id"],))]
        return Entry(**d)

    def get(self, entry_id: int) -> Entry:
        row = self.s.conn.execute("SELECT * FROM knowledge_entries WHERE id=?", (entry_id,)).fetchone()
        if row is None:
            raise KeyError(f"bilgi kaydı yok: {entry_id}")
        return self._entry(row)

    def current(self, inventory_id: int, *, kind: str | None = None,
                confidence: str | None = None, dimension_code: str | None = None) -> list[Entry]:
        q, args = "SELECT * FROM current_knowledge WHERE inventory_id=?", [inventory_id]
        for col, val in (("kind", kind), ("confidence", confidence),
                         ("dimension_code", dimension_code)):
            if val is not None:
                q += f" AND {col}=?"
                args.append(str(val))
        return [self._entry(r) for r in self.s.conn.execute(q + " ORDER BY entry_key", args)]

    def confirmed_rules(self, inventory_id: int) -> list[Entry]:
        return [self._entry(r) for r in self.s.conn.execute(
            "SELECT * FROM confirmed_rules WHERE inventory_id=? ORDER BY entry_key", (inventory_id,))]

    def inferred_rules(self, inventory_id: int) -> list[Entry]:
        return [self._entry(r) for r in self.s.conn.execute(
            "SELECT * FROM inferred_rules WHERE inventory_id=? ORDER BY entry_key", (inventory_id,))]

    def unresolved(self, inventory_id: int) -> list[Entry]:
        return [self._entry(r) for r in self.s.conn.execute(
            "SELECT * FROM unresolved_knowledge WHERE inventory_id=? ORDER BY entry_key",
            (inventory_id,))]

    def history(self, inventory_id: int, entry_key: str) -> list[Entry]:
        return [self._entry(r) for r in self.s.conn.execute(
            "SELECT * FROM knowledge_entries WHERE inventory_id=? AND entry_key=? ORDER BY version",
            (inventory_id, entry_key))]

    def latest(self, inventory_id: int, entry_key: str) -> Entry | None:
        row = self.s.conn.execute(
            "SELECT * FROM current_knowledge WHERE inventory_id=? AND entry_key=?",
            (inventory_id, entry_key)).fetchone()
        return self._entry(row) if row else None

    def learning_history(self, inventory_id: int) -> list[dict]:
        return [dict(r, detail=json.loads(r["detail"])) for r in self.s.conn.execute(
            "SELECT * FROM learning_history WHERE inventory_id=? ORDER BY id", (inventory_id,))]

    def corrections(self, inventory_id: int) -> list[Correction]:
        return [Correction(**dict(r)) for r in self.s.conn.execute(
            "SELECT * FROM user_corrections WHERE inventory_id=? ORDER BY id", (inventory_id,))]

    # -- yazma ----------------------------------------------------------------------------
    @staticmethod
    def _guard(confidence, source, confirmation_ref):
        if confidence == Confidence.CONFIRMED and (
                source not in TRUSTED_SOURCES or not confirmation_ref):
            raise ValueError(
                "CONFIRMED yalnız belge ya da kullanıcı kaynağından ve onay referansıyla verilebilir; "
                "çıkarım ve tahminler doğrulanmış bilgi sayılmaz")

    def _insert(self, c, **row) -> int:
        row["content"] = json.dumps(row["content"], ensure_ascii=False, sort_keys=True)
        for k in ("confidence", "source", "validation"):
            row[k] = str(row[k])
        cols = _COLS.split(", ")
        return c.execute(f"INSERT INTO knowledge_entries({_COLS}) VALUES ({','.join('?' * len(cols))})",
                         [row.get(k) for k in cols]).lastrowid

    def _link(self, c, entry_id: int, evidence_ids, relation="supports"):
        c.executemany("INSERT INTO knowledge_evidence(entry_id, evidence_id, relation) VALUES (?,?,?)",
                      [(entry_id, e, relation) for e in evidence_ids])

    def record(self, inventory_id: int, *, entry_key: str, kind: str, content: dict, summary: str,
               confidence: Confidence | str, source: Source | str, rule_type: str | None = None,
               item_number: int | None = None, dimension_code: str | None = None,
               evidence_ids=(), confirmation_ref: str | None = None, reason: str | None = None,
               created_by: str = "system") -> Entry:
        self._guard(confidence, source, confirmation_ref)
        if self.latest(inventory_id, entry_key) is not None:
            raise ValueError(f"'{entry_key}' zaten var; değişiklik için revise() kullanın")
        validation = Validation.VALIDATED if confidence == Confidence.CONFIRMED else Validation.PENDING
        with self.s.transaction() as c:
            eid = self._insert(c, inventory_id=inventory_id, entry_key=entry_key, version=1,
                               supersedes_id=None, kind=kind, rule_type=rule_type, content=content,
                               summary=summary, item_number=item_number,
                               dimension_code=dimension_code, confidence=confidence, source=source,
                               validation=validation, confirmation_ref=confirmation_ref,
                               reason=reason, created_by=created_by)
            self._link(c, eid, evidence_ids)
        return self.get(eid)

    def _new_version(self, prev: Entry, **changes) -> Entry:
        latest = self.latest(prev.inventory_id, prev.entry_key)
        if latest is None or latest.id != prev.id:
            raise ValueError(f"yalnız en son sürüm üzerinde değişiklik yapılabilir "
                             f"('{prev.entry_key}' güncel sürüm: v{latest.version if latest else '?'})")
        evidence_ids = changes.pop("evidence_ids", ())
        row = {k: getattr(prev, k) for k in (
            "inventory_id", "entry_key", "kind", "rule_type", "content", "summary", "item_number",
            "dimension_code", "confidence", "source", "validation", "confirmation_ref",
            "created_by")}
        row.update({k: v for k, v in changes.items() if v is not None or k == "confirmation_ref"})
        row.update(version=prev.version + 1, supersedes_id=prev.id)
        with self.s.transaction() as c:
            eid = self._insert(c, **row)
            self._link(c, eid, list(dict.fromkeys([*prev.evidence_ids, *evidence_ids])))
        return self.get(eid)

    def revise(self, entry_id: int, *, source: Source | str, reason: str,
               content: dict | None = None, summary: str | None = None,
               confidence: Confidence | str | None = None, evidence_ids=(),
               created_by: str = "system") -> Entry:
        if confidence == Confidence.CONFIRMED:
            raise ValueError("CONFIRMED için confirm() ya da apply_user_correction() kullanın")
        prev = self.get(entry_id)
        return self._new_version(prev, content=content, summary=summary, confidence=confidence,
                                 source=source, reason=reason, validation=Validation.PENDING,
                                 confirmation_ref=None, evidence_ids=evidence_ids,
                                 created_by=created_by)

    def confirm(self, entry_id: int, *, source: Source | str, confirmation_ref: str, reason: str,
                created_by: str = "system") -> Entry:
        self._guard(Confidence.CONFIRMED, source, confirmation_ref)
        return self._new_version(self.get(entry_id), confidence=Confidence.CONFIRMED, source=source,
                                 validation=Validation.VALIDATED, confirmation_ref=confirmation_ref,
                                 reason=reason, created_by=created_by)

    def reject(self, entry_id: int, *, reason: str, by: str = "kullanıcı") -> Entry:
        with self.s.transaction():
            ev = self.s.inventory.add_evidence(kind="user_statement", note=reason)
            return self._new_version(self.get(entry_id), source=Source.USER,
                                     validation=Validation.REJECTED,
                                     confirmation_ref=f"evidence:{ev}", reason=reason,
                                     created_by=by, evidence_ids=[ev])

    def apply_user_correction(self, inventory_id: int, *, entry_key: str, content: dict,
                              summary: str, explanation: str, user_text: str | None,
                              kind: str = "rule", rule_type: str | None = None,
                              item_number: int | None = None, dimension_code: str | None = None,
                              target_entry_id: int | None = None) -> tuple[Correction, Entry]:
        """Kullanıcının verdiği bilgiyi açık bir düzeltme kaydı olarak saklar ve CONFIRMED
        yeni sürüm üretir. Önceki sürümler değişmeden kalır."""
        with self.s.transaction() as c:
            ev = self.s.inventory.add_evidence(kind="user_statement", note=user_text or explanation)
            prev = self.get(target_entry_id) if target_entry_id else self.latest(inventory_id, entry_key)
            ref = f"evidence:{ev}"
            if prev is None:
                entry = self.record(inventory_id, entry_key=entry_key, kind=kind, content=content,
                                    summary=summary, confidence=Confidence.CONFIRMED,
                                    source=Source.USER, rule_type=rule_type,
                                    item_number=item_number, dimension_code=dimension_code,
                                    evidence_ids=[ev], confirmation_ref=ref, reason=explanation,
                                    created_by="kullanıcı")
            else:
                entry = self._new_version(prev, kind=kind, rule_type=rule_type, content=content,
                                          summary=summary, item_number=item_number,
                                          dimension_code=dimension_code,
                                          confidence=Confidence.CONFIRMED, source=Source.USER,
                                          validation=Validation.VALIDATED, confirmation_ref=ref,
                                          reason=explanation, created_by="kullanıcı",
                                          evidence_ids=[ev])
            cid = c.execute("INSERT INTO user_corrections(inventory_id, target_entry_id, "
                            "resulting_entry_id, evidence_id, explanation, user_text) "
                            "VALUES (?,?,?,?,?,?)",
                            (inventory_id, prev.id if prev else None, entry.id, ev, explanation,
                             user_text)).lastrowid
        row = self.s.conn.execute("SELECT * FROM user_corrections WHERE id=?", (cid,)).fetchone()
        return Correction(**dict(row)), entry
