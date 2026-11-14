"""Bilgi bütünlüğü matrisi ve kullanıcıya sorulan netleştirme soruları.

Bir soru `dedupe_key` ile tekildir: açık ya da yanıtlanmış bir soru yeniden oluşturulmaz.
"Sonra sor" yanıtı soruyu bekleyenlerde tutar; "atla" yanıtı verilen soru aynı anahtarla
yeniden gündeme gelirse açılır. Soru içeriği değişmez; durum yalnız yanıtlarla değişir.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from .enums import QuestionStatus, ResponseKind


@dataclass
class Requirement:
    id: int
    inventory_id: int
    req_key: str
    category: str
    description: str
    dimension_code: str | None
    item_number: int | None
    critical: bool
    status: str
    status_reason: str | None
    updated_at: str


@dataclass
class Question:
    id: int
    inventory_id: int
    dedupe_key: str
    group_key: str
    requirement_id: int | None
    missing_info: str
    why_it_matters: str
    related: dict
    evidence_summary: str
    impact: str
    question_text: str
    choices: list[str] | None
    status: str
    created_at: str
    updated_at: str


class QuestionRepo:
    def __init__(self, store):
        self.s = store

    # -- gereksinimler --------------------------------------------------------------------
    @staticmethod
    def _req(row) -> Requirement:
        d = dict(row)
        d["critical"] = bool(d["critical"])
        return Requirement(**d)

    def upsert_requirement(self, inventory_id: int, *, req_key: str, category: str,
                           description: str, critical: bool, status: str, reason: str | None,
                           dimension_code: str | None = None,
                           item_number: int | None = None) -> Requirement:
        with self.s.transaction() as c:
            row = c.execute("SELECT id FROM requirements WHERE inventory_id=? AND req_key=?",
                            (inventory_id, req_key)).fetchone()
            if row is None:
                c.execute("INSERT INTO requirements(inventory_id, req_key, category, description, "
                          "dimension_code, item_number, critical, status, status_reason) "
                          "VALUES (?,?,?,?,?,?,?,?,?)",
                          (inventory_id, req_key, category, description, dimension_code,
                           item_number, int(critical), str(status), reason))
            else:
                c.execute("UPDATE requirements SET category=?, description=?, dimension_code=?, "
                          "item_number=?, critical=?, status=?, status_reason=?, "
                          "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?",
                          (category, description, dimension_code, item_number, int(critical),
                           str(status), reason, row[0]))
        return self.requirement(inventory_id, req_key)

    def requirement(self, inventory_id: int, req_key: str) -> Requirement:
        row = self.s.conn.execute("SELECT * FROM requirements WHERE inventory_id=? AND req_key=?",
                                  (inventory_id, req_key)).fetchone()
        if row is None:
            raise KeyError(req_key)
        return self._req(row)

    def matrix(self, inventory_id: int) -> list[Requirement]:
        return [self._req(r) for r in self.s.conn.execute(
            "SELECT * FROM requirements WHERE inventory_id=? ORDER BY dimension_code, req_key",
            (inventory_id,))]

    # -- sorular --------------------------------------------------------------------------
    @staticmethod
    def _q(row) -> Question:
        d = dict(row)
        d["related"] = json.loads(d["related"])
        d["choices"] = json.loads(d["choices"]) if d["choices"] else None
        return Question(**d)

    def get(self, question_id: int) -> Question:
        row = self.s.conn.execute("SELECT * FROM clarification_questions WHERE id=?",
                                  (question_id,)).fetchone()
        if row is None:
            raise KeyError(question_id)
        return self._q(row)

    def ask(self, inventory_id: int, *, dedupe_key: str, group_key: str, missing_info: str,
            why_it_matters: str, related: dict, evidence_summary: str, impact: str,
            question_text: str, choices: list[str] | None = None,
            requirement_id: int | None = None) -> tuple[Question, bool]:
        with self.s.transaction() as c:
            row = c.execute("SELECT * FROM clarification_questions WHERE inventory_id=? AND "
                            "dedupe_key=?", (inventory_id, dedupe_key)).fetchone()
            if row is not None:
                if row["status"] == QuestionStatus.SKIPPED:
                    c.execute("UPDATE clarification_questions SET status='open', "
                              "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?",
                              (row["id"],))
                    c.execute("INSERT INTO learning_history(inventory_id, event, ref_table, ref_id, "
                              "detail) VALUES (?, 'question_reopened', 'clarification_questions', "
                              "?, json_object('dedupe_key', ?))",
                              (inventory_id, row["id"], dedupe_key))
                return self.get(row["id"]), False
            qid = c.execute(
                "INSERT INTO clarification_questions(inventory_id, dedupe_key, group_key, "
                "requirement_id, missing_info, why_it_matters, related, evidence_summary, impact, "
                "question_text, choices) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (inventory_id, dedupe_key, group_key, requirement_id, missing_info, why_it_matters,
                 json.dumps(related, ensure_ascii=False, sort_keys=True), evidence_summary, impact,
                 question_text, json.dumps(choices, ensure_ascii=False) if choices else None)
            ).lastrowid
        return self.get(qid), True

    def respond(self, question_id: int, kind: ResponseKind | str, value: str | None = None) -> Question:
        q = self.get(question_id)
        kind = ResponseKind(kind)
        if kind == ResponseKind.CHOICE and (not q.choices or value not in q.choices):
            raise ValueError(f"geçersiz seçim: {value!r}; seçenekler: {q.choices}")
        if kind == ResponseKind.TEXT and not (value or "").strip():
            raise ValueError("açıklama boş olamaz")
        if kind not in (ResponseKind.CHOICE, ResponseKind.TEXT):
            value = None
        with self.s.transaction() as c:
            c.execute("INSERT INTO question_responses(question_id, kind, value) VALUES (?,?,?)",
                      (question_id, str(kind), value))
        return self.get(question_id)

    def responses(self, question_id: int) -> list[dict]:
        return [dict(r) for r in self.s.conn.execute(
            "SELECT * FROM question_responses WHERE question_id=? ORDER BY id", (question_id,))]

    def pending(self, inventory_id: int) -> list[Question]:
        return [self._q(r) for r in self.s.conn.execute(
            "SELECT * FROM pending_questions WHERE inventory_id=?", (inventory_id,))]

    def all(self, inventory_id: int) -> list[Question]:
        return [self._q(r) for r in self.s.conn.execute(
            "SELECT * FROM clarification_questions WHERE inventory_id=? ORDER BY group_key, id",
            (inventory_id,))]
