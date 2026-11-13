import pytest

from lai_runtime.client import (ModelNotFoundError, OllamaClient, RuntimeUnavailableError,
                                StructuredOutputError)
from conftest import free_port


@pytest.mark.parametrize("url", ["http://0.0.0.0:11435", "http://192.168.1.2:11435",
                                 "http://example.com:11435", "https://127.0.0.1:11435",
                                 "http://127.0.0.1.evil.com:80"])
def test_non_loopback_rejected(url):
    with pytest.raises(ValueError):
        OllamaClient(url)


def test_version_and_models(fake_server):
    base, _, register = fake_server
    register("qwen3.5:4b")
    c = OllamaClient(base)
    assert c.version() == "0.0.0-fake"
    assert [m["name"] for m in c.list_models()] == ["qwen3.5:4b"]


def test_unavailable_runtime():
    with pytest.raises(RuntimeUnavailableError):
        OllamaClient(f"http://127.0.0.1:{free_port()}", timeout=2).version()


def test_model_not_found(fake_server):
    base, _, _ = fake_server
    c = OllamaClient(base)
    with pytest.raises(ModelNotFoundError) as e:
        c.generate("yok:1b", "merhaba")
    assert "yok:1b" in str(e.value)
    with pytest.raises(ModelNotFoundError):
        c.show("yok:1b")


def test_generate_plain(fake_server):
    base, store, register = fake_server
    register("m:latest")
    r = OllamaClient(base).generate("m", "Sadece TAMAM yaz.", system="Türkçe yanıt ver.")
    assert r.text == "TAMAM"
    assert r.data is None


SCHEMA = {"type": "object", "properties": {
    "boyut": {"type": "string", "enum": ["E-I", "S-N", "T-F", "J-P"]},
    "madde": {"type": "integer", "minimum": 1}}, "required": ["boyut", "madde"],
    "additionalProperties": False}


def test_generate_structured(fake_server):
    base, _, register = fake_server
    register("m:latest")
    r = OllamaClient(base).generate("m", "analiz et", schema=SCHEMA)
    assert r.data == {"boyut": "E-I", "madde": 1}


@pytest.mark.parametrize("prompt", ["BOZUK_JSON", "SEMA_DISI"])
def test_generate_structured_invalid(fake_server, prompt):
    base, _, register = fake_server
    register("m:latest")
    with pytest.raises(StructuredOutputError):
        OllamaClient(base).generate("m", prompt, schema=SCHEMA)


def test_cpu_option_and_think_flag_sent(fake_server, monkeypatch):
    base, _, register = fake_server
    register("m:latest")
    c = OllamaClient(base)
    sent = {}
    orig = c._request

    def spy(method, path, body=None, **kw):
        if path == "/api/generate":
            sent.update(body)
        return orig(method, path, body, **kw)

    monkeypatch.setattr(c, "_request", spy)
    c.generate("m", "x", options={"num_gpu": 0, "seed": 7})
    assert sent["think"] is False and sent["stream"] is False
    assert sent["options"] == {"num_gpu": 0, "seed": 7}


def test_pull_reports_progress(fake_server):
    base, _, _ = fake_server
    seen = []
    OllamaClient(base).pull("qwen3.5:4b", on_progress=seen.append)
    assert seen[-1]["status"] == "success"
    assert any(e.get("completed") == 50 for e in seen)
