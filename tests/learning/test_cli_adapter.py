"""Gerçek Ollama bağdaştırıcısı (sahte sunucuya karşı) ve uçtan uca CLI."""
import json
import os
import subprocess
import sys

import pytest
from conftest import FAKE, free_port, wait_http
from synthetic_docs import make_doc_inventory, write_guide_pdf, write_json
from synthetic_rules import make_examples

from lai.ai.llm import LLMUnavailable, OllamaLLM
from lai.ai.prompts import OUTPUT_SCHEMA
from lai.ingest.__main__ import main as ingest_main
from lai.learning.__main__ import main


@pytest.fixture
def fake(tmp_path):
    port = free_port()
    store = tmp_path / "fake-store"
    store.mkdir()
    (store / "fake-models.json").write_text(json.dumps({"qwen3.5:4b": {"digest": "sha256:" + "0" * 64}}))
    env = {"PATH": os.environ["PATH"], "OLLAMA_NO_CLOUD": "1",
           "OLLAMA_HOST": f"127.0.0.1:{port}", "OLLAMA_MODELS": str(store)}
    p = subprocess.Popen([sys.executable, str(FAKE), "serve"], env=env)
    assert wait_http(f"http://127.0.0.1:{port}/api/version")
    yield port, store
    p.terminate()
    p.wait(5)


def test_adapter_returns_raw_text_when_schema_violated(fake):
    port, _ = fake
    llm = OllamaLLM(f"http://127.0.0.1:{port}", "qwen3.5:4b")
    r = llm.complete(system="s", prompt="BOZUK_JSON", schema=OUTPUT_SCHEMA)
    assert r.text == '{"eksik": '
    ok = llm.complete(system="s", prompt="normal", schema=OUTPUT_SCHEMA)
    assert json.loads(ok.text)["candidates"]


def test_adapter_sends_deterministic_options_and_think():
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    seen = []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            seen.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            body = json.dumps({"response": '{"candidates": []}', "done": True}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        OllamaLLM(f"http://127.0.0.1:{srv.server_port}", "qwen3.5:4b").complete(
            system="s", prompt="p", schema=OUTPUT_SCHEMA)
    finally:
        srv.shutdown()
    gen = seen[0]
    # bağlam penceresi açıkça verilir: varsayılan pencere istemin başını (sistem talimatlarını)
    # sessizce kesebilir; düşünme çıktısı da sınırlanır
    assert gen["options"] == {"temperature": 0, "seed": 0, "num_ctx": 12288, "num_predict": 6144}
    assert gen["think"] is True
    assert gen["format"] == OUTPUT_SCHEMA and gen["system"] == "s" and gen["stream"] is False


def test_adapter_unavailable_and_missing_model(fake):
    port, _ = fake
    with pytest.raises(LLMUnavailable):
        OllamaLLM(f"http://127.0.0.1:{free_port()}", "x").complete(system="s", prompt="p",
                                                                   schema=OUTPUT_SCHEMA)
    with pytest.raises(LLMUnavailable, match="model"):
        OllamaLLM(f"http://127.0.0.1:{port}", "yok:1b").complete(system="s", prompt="p",
                                                                 schema=OUTPUT_SCHEMA)


@pytest.mark.parametrize("url", ["http://localhost:11435", "http://192.168.1.5:11435",
                                 "https://127.0.0.1:11435"])
def test_adapter_refuses_non_loopback(url):
    with pytest.raises(ValueError):
        OllamaLLM(url, "m")


@pytest.fixture
def db(tmp_path):
    inv = make_doc_inventory(24)
    for it in inv["items"]:
        it["options"] = it["options"][:2]
    db = tmp_path / "veri dizini" / "lai.db"
    assert ingest_main(["import", "--db", str(db), "--inventory", "Sentetik İş Envanteri",
                        str(write_json(inv, tmp_path / "envanter.json"))]) == 0
    return db


def test_cli_full_flow_against_fake_runtime(fake, db, tmp_path, capsys):
    port, store = fake
    base = ["--db", str(db), "--inventory", "Sentetik İş Envanteri"]
    guide = write_guide_pdf(tmp_path / "yönerge.pdf")
    assert main(["reference", *base, str(guide)]) == 0
    assert "KAYDEDİLDİ" in capsys.readouterr().out
    ex = tmp_path / "örnek.json"
    ex.write_text(json.dumps(make_examples(24, 5, {12}), ensure_ascii=False))
    assert main(["examples", *base, str(ex)]) == 0
    assert "5 örnek" in capsys.readouterr().out
    assert main(["discover", *base, "--port", str(port), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    # sahte sunucu şemaya biçimce uyan ama anlamsız öneri üretir → hepsi reddedilir
    assert out["status"] == "completed" and out["accepted"] == 0
    assert out["rejected"] == out["candidates"] > 0
    assert {r["code"] for r in out["rejections"]} == {"invalid_body"}
    assert main(["rules", *base]) == 0
    assert "Kayıtlı kural yok" in capsys.readouterr().out
    assert main(["fit", *base]) == 0
    log = (store / "requests.log").read_text()
    assert "/api/pull" not in log and "/api/create" not in log  # model indirilmez/oluşturulmaz


def test_cli_refuses_system_ollama_port(db, capsys):
    rc = main(["discover", "--db", str(db), "--inventory", "Sentetik İş Envanteri",
               "--port", "11434"])
    assert rc == 2 and "11434" in capsys.readouterr().err


def test_cli_runtime_down_fails_cleanly(db, capsys):
    rc = main(["discover", "--db", str(db), "--inventory", "Sentetik İş Envanteri",
               "--port", str(free_port())])
    out = capsys.readouterr().out
    assert rc == 1 and "BAŞARISIZ" in out and "ulaşılamadı" in out


def test_cli_unknown_inventory(db, capsys):
    assert main(["rules", "--db", str(db), "--inventory", "Yok"]) == 2
    assert "envanter yok" in capsys.readouterr().err
