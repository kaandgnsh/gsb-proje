import pytest

from lai_runtime.chunking import chunk_text
from lai_runtime.jsonschema_lite import validate

S = {"type": "object", "required": ["a"], "additionalProperties": False,
     "properties": {"a": {"type": "integer", "minimum": 1, "maximum": 5},
                    "b": {"type": "array", "items": {"type": "string", "enum": ["x", "y"]}},
                    "c": {"type": ["string", "null"]}}}


@pytest.mark.parametrize("inst,ok", [
    ({"a": 1}, True), ({"a": 5, "b": ["x", "y"], "c": None}, True),
    ({}, False), ({"a": 0}, False), ({"a": 6}, False), ({"a": True}, False),
    ({"a": 1.5}, False), ({"a": 1, "z": 1}, False), ({"a": 1, "b": ["q"]}, False),
    ({"a": 1, "c": 3}, False), ([], False)])
def test_validate(inst, ok):
    assert (validate(inst, S) == []) is ok


def test_validate_reports_path():
    errs = validate({"a": 1, "b": ["x", "q"]}, S)
    assert any("b[1]" in e for e in errs)


def _doc(n):
    paras = []
    for i in range(1, n + 1):
        paras.append(f"Madde {i}. Kişi kalabalık ortamlarda enerji kazanır mı? "
                     f"Bu madde dışadönüklük boyutuyla ilgilidir. Örnek cümle {i} burada biter.")
    return "\n\n".join(paras)


def test_chunks_cover_text_exactly():
    text = _doc(120)
    chunks = chunk_text(text, max_chars=1500, overlap=150)
    assert len(chunks) > 5
    assert chunks[0].start == 0 and chunks[-1].end == len(text)
    for prev, cur in zip(chunks, chunks[1:], strict=False):
        assert cur.start <= prev.end  # boşluk yok
        assert cur.start >= prev.start
    for c in chunks:
        assert text[c.start:c.end] == c.text
        assert len(c.text) <= 1500
    assert [c.index for c in chunks] == list(range(len(chunks)))


def test_chunks_prefer_paragraph_boundaries():
    text = _doc(60)
    for c in chunk_text(text, max_chars=1200, overlap=0)[:-1]:
        assert text[c.end - 1] in ".\n" or text[c.end:c.end + 2] == "\n\n"


def test_short_text_single_chunk_and_hard_cut():
    assert len(chunk_text("kısa", 100)) == 1
    hard = chunk_text("a" * 1000, max_chars=300, overlap=0)
    assert "".join(c.text for c in hard) == "a" * 1000


def test_invalid_args():
    with pytest.raises(ValueError):
        chunk_text("x", max_chars=100, overlap=100)
