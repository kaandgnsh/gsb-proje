import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

FAKE = Path(__file__).with_name("fake_ollama.py")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_http(url, timeout=10):
    end = time.time() + timeout
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    while time.time() < end:
        try:
            with opener.open(url, timeout=1):
                return True
        except OSError:
            time.sleep(0.05)
    return False


@pytest.fixture
def fake_server(tmp_path):
    """Doğrudan başlatılan sahte sunucu: (base_url, store_dir, register_model)."""
    port = free_port()
    store = tmp_path / "fake-store"
    env = {"PATH": os.environ["PATH"], "OLLAMA_NO_CLOUD": "1",
           "OLLAMA_HOST": f"127.0.0.1:{port}", "OLLAMA_MODELS": str(store)}
    p = subprocess.Popen([sys.executable, str(FAKE), "serve"], env=env)
    base = f"http://127.0.0.1:{port}"
    assert wait_http(base + "/api/version"), "sahte sunucu açılmadı"

    def register(name, digest="sha256:" + "0" * 64):
        f = store / "fake-models.json"
        reg = json.loads(f.read_text()) if f.exists() else {}
        reg[name] = {"digest": digest}
        f.write_text(json.dumps(reg))

    yield base, store, register
    p.terminate()
    p.wait(5)


@pytest.fixture
def fake_binary(tmp_path):
    """RuntimeManager'ın `<binary> serve` olarak çalıştıracağı sahte ikili."""
    b = tmp_path / "bin" / "ollama"
    b.parent.mkdir()
    b.write_text(f"#!/bin/sh\nexec {sys.executable} {FAKE} \"$@\"\n")
    b.chmod(0o755)
    return b
