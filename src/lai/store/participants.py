"""Katılımcılar ve cevap setleri. Tek zorunlu alan anonim katılımcı kodudur."""
from __future__ import annotations

import json
import secrets
import sqlite3
from dataclasses import dataclass

_MUTABLE = ("alias", "full_name", "contact", "category", "attributes")


@dataclass
class Participant:
    id: int
    code: str
    alias: str | None
    full_name: str | None
    contact: str | None
    category: str | None
    attributes: dict
    created_at: str
    updated_at: str


class ParticipantRepo:
    def __init__(self, store):
        self.s = store

    @staticmethod
    def _p(row) -> Participant:
        d = dict(row)
        d["attributes"] = json.loads(d["attributes"])
        return Participant(**d)

    def create(self, *, code: str | None = None, alias: str | None = None,
               full_name: str | None = None, contact: str | None = None,
               category: str | None = None, attributes: dict | None = None) -> Participant:
        code = code or f"ANON-{secrets.token_hex(4).upper()}"
        try:
            with self.s.transaction() as c:
                pid = c.execute("INSERT INTO participants(code, alias, full_name, contact, category, "
                                "attributes) VALUES (?,?,?,?,?,?)",
                                (code, alias, full_name, contact, category,
                                 json.dumps(attributes or {}, ensure_ascii=False))).lastrowid
        except sqlite3.IntegrityError:
            raise ValueError(f"'{code}' kodlu katılımcı zaten var") from None
        return self.get(pid)

    def get(self, participant_id: int) -> Participant:
        row = self.s.conn.execute("SELECT * FROM participants WHERE id=?", (participant_id,)).fetchone()
        if row is None:
            raise KeyError(participant_id)
        return self._p(row)

    def find_by_code(self, code: str) -> Participant | None:
        row = self.s.conn.execute("SELECT * FROM participants WHERE code=?", (code,)).fetchone()
        return self._p(row) if row else None

    def list(self) -> list[Participant]:
        return [self._p(r) for r in self.s.conn.execute("SELECT * FROM participants ORDER BY id")]

    def update_details(self, participant_id: int, **fields) -> Participant:
        bad = set(fields) - set(_MUTABLE)
        if bad:
            raise ValueError(f"değiştirilemeyen alan(lar): {', '.join(sorted(bad))}")
        if "attributes" in fields:
            fields["attributes"] = json.dumps(fields["attributes"] or {}, ensure_ascii=False)
        sets = ", ".join(f"{k}=?" for k in fields)
        with self.s.transaction() as c:
            c.execute(f"UPDATE participants SET {sets}, updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
                      "WHERE id=?", [*fields.values(), participant_id])
        return self.get(participant_id)

    def add_response_set(self, participant_id: int, inventory_version_id: int,
                         responses: dict[int, str | None], source_label: str | None = None) -> int:
        if any(not isinstance(n, int) or n < 1 for n in responses):
            raise ValueError("madde numaraları 1 ya da daha büyük tamsayı olmalı")
        with self.s.transaction() as c:
            rs = c.execute("INSERT INTO response_sets(participant_id, inventory_version_id, "
                           "source_label) VALUES (?,?,?)",
                           (participant_id, inventory_version_id, source_label)).lastrowid
            c.executemany("INSERT INTO responses VALUES (?,?,?)",
                          [(rs, n, v) for n, v in sorted(responses.items())])
        return rs

    def responses(self, response_set_id: int) -> dict[int, str | None]:
        return {r[0]: r[1] for r in self.s.conn.execute(
            "SELECT item_number, option_code FROM responses WHERE response_set_id=? "
            "ORDER BY item_number", (response_set_id,))}

    def response_sets(self, participant_id: int) -> list[dict]:
        return [dict(r) for r in self.s.conn.execute(
            "SELECT * FROM response_sets WHERE participant_id=? ORDER BY id", (participant_id,))]
