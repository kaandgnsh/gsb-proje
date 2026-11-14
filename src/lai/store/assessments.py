"""Değerlendirme kayıtları: değişmez. Yeni bilgiyle yeniden hesaplama yeni kayıt üretir
(`recalculated_from`); eski sonuç olduğu gibi kalır."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from .enums import AssessmentStatus


@dataclass
class Assessment:
    id: int
    response_set_id: int
    profile_version_id: int
    status: str
    results: dict
    limitations: list[str]
    engine_version: str
    input_hash: str
    recalculated_from: int | None
    created_at: str


def _canonical(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class AssessmentRepo:
    def __init__(self, store):
        self.s = store

    @staticmethod
    def _a(row) -> Assessment:
        d = dict(row)
        d["results"] = json.loads(d["results"])
        d["limitations"] = json.loads(d["limitations"])
        return Assessment(**d)

    def input_hash(self, response_set_id: int, profile_version_id: int, engine_version: str) -> str:
        responses = self.s.participants.responses(response_set_id)
        profile = self.s.profiles.get_version(profile_version_id)
        payload = {"responses": {str(k): v for k, v in responses.items()},
                   "profile_hash": profile.content_hash, "engine": engine_version}
        return hashlib.sha256(_canonical(payload).encode()).hexdigest()

    def record(self, *, response_set_id: int, profile_version_id: int,
               status: AssessmentStatus | str, results: dict, limitations: list[str],
               engine_version: str, recalculated_from: int | None = None) -> Assessment:
        profile = self.s.profiles.get_version(profile_version_id)
        if status == AssessmentStatus.VERIFIED and profile.provisional:
            raise ValueError("geçici (provisional) profil sürümüyle VERIFIED değerlendirme "
                             "kaydedilemez; sonuç PROVISIONAL olmalı")
        h = self.input_hash(response_set_id, profile_version_id, engine_version)
        with self.s.transaction() as c:
            aid = c.execute("INSERT INTO assessments(response_set_id, profile_version_id, status, "
                            "results, limitations, engine_version, input_hash, recalculated_from) "
                            "VALUES (?,?,?,?,?,?,?,?)",
                            (response_set_id, profile_version_id, str(status), _canonical(results),
                             _canonical(limitations), engine_version, h,
                             recalculated_from)).lastrowid
        return self.get(aid)

    def get(self, assessment_id: int) -> Assessment:
        row = self.s.conn.execute("SELECT * FROM assessments WHERE id=?", (assessment_id,)).fetchone()
        if row is None:
            raise KeyError(assessment_id)
        return self._a(row)

    def for_response_set(self, response_set_id: int) -> list[Assessment]:
        return [self._a(r) for r in self.s.conn.execute(
            "SELECT * FROM assessments WHERE response_set_id=? ORDER BY id", (response_set_id,))]

    def add_feedback(self, assessment_id: int, text: str) -> int:
        if not text.strip():
            raise ValueError("geri bildirim boş olamaz")
        with self.s.transaction() as c:
            return c.execute("INSERT INTO assessment_feedback(assessment_id, feedback) VALUES (?,?)",
                             (assessment_id, text)).lastrowid

    def feedback(self, assessment_id: int) -> list[dict]:
        return [dict(r) for r in self.s.conn.execute(
            "SELECT * FROM assessment_feedback WHERE assessment_id=? ORDER BY id", (assessment_id,))]
