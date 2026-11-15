import csv
import json
import sqlite3

import pymupdf
import pytest
from openpyxl import load_workbook
from synthetic_desktop import ready

from lai.ai.llm import LLMError, LLMReply, LLMUnavailable
from lai.assessment.service import submit_single
from lai.reports import commentary, service
from lai.store.store import Store

GOOD = {
    "summary": "Sonuç ESFJ olarak kayıtlıdır ve doğrulanmış kurallarla hesaplanmıştır.",
    "dimensions": [
        {"dimension": "E-I", "text": "E-I boyutunda E kutbu 2 puanla seçildi."},
        {"dimension": "S-N", "text": "S-N boyutunda S kutbu öne çıktı."},
        {"dimension": "T-F", "text": "T ve F eşit (1); eşitlik kuralı F seçti."},
        {"dimension": "J-P", "text": "J-P boyutunda J seçildi, P 0 puan aldı."},
    ],
}


@pytest.fixture
def saved(tmp_path):
    db = tmp_path / "s.db"
    _, iv, _, pv = ready(db)
    store = Store.open(db)
    result = submit_single(
        store,
        inventory_version_id=iv,
        profile_version_id=pv,
        participant_code="Çığ-İş",
        alias="Özgür Şule",
        responses=[(n, "A") for n in range(1, 9)],
    )
    yield store, result["assessment_id"]
    store.close()


class FakeLLM:
    model, think = "yerel-test", True

    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def complete(self, *, system, prompt, schema):
        self.calls.append({"system": system, "prompt": prompt, "schema": schema})
        if isinstance(self.reply, Exception):
            raise self.reply
        text = self.reply if isinstance(self.reply, str) else json.dumps(self.reply)
        return LLMReply(text, "", 1, 1)


def llm(reply):
    return FakeLLM(reply)


def stored(store):
    return store.conn.execute("SELECT count(*) FROM report_commentaries").fetchone()[0]


def test_valid_commentary_is_stored_and_exported(saved, tmp_path, monkeypatch):
    s, aid = saved
    before = s.assessments.get(aid)
    fake = llm(GOOD)
    body = commentary.generate(s, aid, fake)
    assert body["summary"] == GOOD["summary"] and body["model"] == "yerel-test"
    assert s.assessments.get(aid) == before
    prompt = fake.calls[0]["prompt"]
    assert "Çığ-İş" not in prompt and "Özgür" not in prompt
    assert json.loads(prompt)["profile_code"] == "ESFJ"

    import socket

    def forbidden(*a, **kw):
        raise AssertionError("Dışa aktarma ağ çağırdı")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    report = service.assessment_report(s, aid)
    assert report["ai_commentary"]["dimensions"][0]["text"] == GOOD["dimensions"][0]["text"]
    for format in ["json", "csv", "xlsx", "pdf"]:
        target = tmp_path / f"açıklama.{format}"
        service.export_assessment(s, aid, target, format)
        if format == "json":
            text = target.read_text()
        elif format == "csv":
            text = str(list(csv.reader(target.open(encoding="utf-8-sig"))))
        elif format == "xlsx":
            wb = load_workbook(target)
            text = str(list(wb["Model açıklaması"].values))
            wb.close()
        else:
            with pymupdf.open(target) as doc:
                text = " ".join(" ".join(p.get_text().split()) for p in doc)
        assert GOOD["summary"] in text
        assert "puanı ve seçilen kutbu değiştirmez" in text or format == "json"


def test_no_commentary_keeps_template_report(saved):
    s, aid = saved
    report = service.assessment_report(s, aid)
    assert report["ai_commentary"] is None
    assert [n for n, _ in service.single_tables(report)][-1] == "Açıklamalar"


@pytest.mark.parametrize(
    "reply, message",
    [
        ("düz metin", "JSON"),
        ({"summary": "x", "dimensions": GOOD["dimensions"][:3]}, "tam olarak bir"),
        (
            {**GOOD, "summary": "Katılımcı yüzde 87 dışadönük."},
            "kayıtta olmayan sayı",
        ),
        (
            {
                **GOOD,
                "dimensions": [
                    {"dimension": "E-I", "text": "Bu boyutta I kutbu baskın."},
                    *GOOD["dimensions"][1:],
                ],
            },
            "seçilen kutup",
        ),
        ({**GOOD, "summary": "Profil INTP olarak belirlendi."}, "profil kodu"),
        ({**GOOD, "extra": 1}, "beklenen alanları"),
    ],
)
def test_invalid_output_writes_nothing(saved, reply, message):
    s, aid = saved
    with pytest.raises(commentary.CommentaryError, match=message):
        commentary.generate(s, aid, llm(reply))
    assert stored(s) == 0
    assert service.assessment_report(s, aid)["ai_commentary"] is None


@pytest.mark.parametrize("error", [LLMUnavailable("kapalı"), LLMError("500")])
def test_model_failure_writes_nothing(saved, error):
    s, aid = saved
    with pytest.raises(commentary.CommentaryError, match="Yerel model"):
        commentary.generate(s, aid, llm(error))
    assert stored(s) == 0


def test_not_evaluable_dimension_must_not_name_a_pole():
    f = {
        "status": "PROVISIONAL",
        "profile_code": None,
        "limitations": [],
        "dimensions": [
            {
                "dimension": "E-I",
                "poles": ["E", "I"],
                "status": "NOT_EVALUABLE",
                "selected_pole": None,
                "scores": None,
                "decided_by": None,
                "limitations": ["eşik bilinmiyor"],
            }
        ],
    }
    ok = {"summary": "Sonuç geçicidir.", "dimensions": [
        {"dimension": "E-I", "text": "E-I hesaplanamadı: eşik bilinmiyor."}]}
    assert commentary.validate(json.dumps(ok), f)["dimensions"][0]["dimension"] == "E-I"
    bad = {"summary": "Sonuç geçicidir.", "dimensions": [
        {"dimension": "E-I", "text": "Muhtemelen E yönünde."}]}
    with pytest.raises(commentary.CommentaryError, match="hesaplanamayan"):
        commentary.validate(json.dumps(bad), f)


def test_commentary_rows_are_append_only(saved):
    s, aid = saved
    commentary.generate(s, aid, llm(GOOD))
    with pytest.raises(sqlite3.IntegrityError, match="değiştirilemez"):
        s.conn.execute("UPDATE report_commentaries SET model='x'")
    with pytest.raises(sqlite3.IntegrityError, match="silinemez"):
        s.conn.execute("DELETE FROM report_commentaries")
    newer = dict(GOOD, summary="İkinci açıklama; sonuç ESFJ.")
    commentary.generate(s, aid, llm(newer))
    assert stored(s) == 2
    assert service.assessment_report(s, aid)["ai_commentary"]["summary"] == newer["summary"]
