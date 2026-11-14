"""Gerçek Ollama ikilisi + gerçek model ile entegrasyon testleri.

Çalıştırma:  .venv/bin/pytest -m integration tests/integration -v
Ayarlar (ortam değişkeni):
  LAI_IT_SOURCE_DIR    modelin salt okunur kopyalanacağı Ollama deposu (vars. /var/lib/ollama)
  LAI_IT_SOURCE_MODEL  o depodaki model adı (vars. depodaki ilk model)
  LAI_IT_WORKDIR       çalışma dizini, disk üzerinde olmalı (vars. <repo>/.it-work)
Sistemdeki Ollama servisine hiçbir istek/sinyal gönderilmez; yalnız durumu okunur.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import textwrap
import time
import urllib.request
from dataclasses import replace
from pathlib import Path

import pytest

from conftest import free_port
from lai_runtime.chunking import chunk_text
from lai_runtime.client import ModelNotFoundError
from lai_runtime.config import RuntimeConfig
from lai_runtime.models import ModelSourceError, find_source_model, import_model
from lai_runtime.netaudit import NetworkMonitor
from lai_runtime.ports import PortConflictError, port_in_use
from lai_runtime.process import RuntimeManager
from lai_setup.paths import resolve_paths

pytestmark = pytest.mark.integration
# Kategoriler: işaretsiz = technical (kod/entegrasyon). model_quality = modelin dil/akıl
# yürütme kalitesi; başarısızlığı kod hatası değil, model sınırlamasıdır ve ayrı raporlanır.
REPO = Path(__file__).resolve().parents[2]
SRC_DIR = Path(os.environ.get("LAI_IT_SOURCE_DIR", "/var/lib/ollama"))
WORK = Path(os.environ.get("LAI_IT_WORKDIR", REPO / ".it-work"))
TARGET = "lai-it:latest"
BINARY = shutil.which("ollama")
OPTS = {"temperature": 0, "seed": 42, "num_ctx": 4096}


def _first_model(store: Path) -> str | None:
    for layout in ("manifests-v2", "manifests"):
        for tag in sorted((store / layout).glob("*/*/*/*")):
            return f"{tag.parent.name}:{tag.name}"
    return None


def _system_snapshot() -> dict:
    """Sistem Ollama'sının gözlemlenebilir durumu (yalnız okuma)."""
    show = subprocess.run(["systemctl", "show", "ollama", "-p", "MainPID,ActiveState,NRestarts"],
                          capture_output=True, text=True).stdout.strip()
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open("http://127.0.0.1:11434/api/tags", timeout=5) as r:
            tags = sorted((m["name"], m["digest"]) for m in json.load(r)["models"])
    except OSError:
        tags = None
    files = {}
    for p in sorted(SRC_DIR.rglob("*")):
        try:
            st = p.lstat()
            files[str(p)] = (st.st_size, st.st_mtime_ns)
        except OSError:
            files[str(p)] = "okunamadı"
    return {"service": show, "tags": tags, "files": files}


@pytest.fixture(scope="session")
def it():
    if not BINARY:
        pytest.fail("ollama ikilisi bulunamadı (entegrasyon testi gerçek ikili ister)")
    model = os.environ.get("LAI_IT_SOURCE_MODEL") or _first_model(SRC_DIR)
    if not model:
        pytest.fail(f"{SRC_DIR} içinde içe aktarılacak model yok; LAI_IT_SOURCE_MODEL verin")
    WORK.mkdir(parents=True, exist_ok=True)
    paths = resolve_paths("linux", {"LAI_HOME": str(WORK / "lai")}, WORK)
    before = _system_snapshot()
    cfg = RuntimeConfig(port=free_port(), model=TARGET, binary=BINARY)
    mgr = RuntimeManager(cfg, paths, base_env={"PATH": os.environ["PATH"]})
    t = time.time()
    mgr.start(timeout=60)
    client = mgr.client()
    if TARGET not in [m["name"] for m in client.list_models()]:
        import_model(client, find_source_model(SRC_DIR, model), TARGET)
    info = {"source_model": model, "import_seconds": round(time.time() - t, 1),
            "ollama_version": client.version()}
    yield {"mgr": mgr, "client": client, "paths": paths, "cfg": cfg, "before": before,
           "info": info}
    mgr.stop()


def _gen(it, prompt, **kw):
    return it["client"].generate(TARGET, prompt, options={**OPTS, **kw.pop("options", {})}, **kw)


def test_01_isolated_runtime_generates(it):
    r = _gen(it, "Yalnızca TAMAM kelimesini yaz, başka hiçbir şey yazma.")
    print("\nyanıt:", repr(r.text), "| süre:", r.total_duration_ns / 1e9, "sn |", it["info"])
    assert "TAMAM" in r.text.upper()
    env = Path(f"/proc/{it['mgr'].status().pid}/environ").read_bytes().split(b"\0")
    assert b"OLLAMA_NO_CLOUD=1" in env
    assert f"OLLAMA_HOST=127.0.0.1:{it['cfg'].port}".encode() in env
    assert f"OLLAMA_MODELS={it['paths'].models}".encode() in env


@pytest.mark.model_quality
@pytest.mark.parametrize("prompt,expected", [
    ("İstanbul Türkiye'de midir? Yanıtını yalnızca EVET ya da HAYIR olarak, büyük harfle ver.", "EVET"),
    ("Şu cümlede kaç kelime var: 'Bugün hava çok güzel.' Yanıtı yalnızca rakamla ver.", "4"),
    ("'kitap' kelimesinin çoğul hâlini yaz. Yalnızca kelimeyi yaz.", "kitaplar"),
])
def test_02_turkish_instruction_following(it, prompt, expected):
    r = _gen(it, prompt, system="Sen Türkçe yanıt veren, talimatlara harfiyen uyan bir asistansın.")
    print(f"\n{prompt!r} -> {r.text!r}")
    assert expected.lower() in r.text.strip().lower()


SCHEMA = {"type": "object", "additionalProperties": False,
          "required": ["boyut", "kutup", "gerekce"],
          "properties": {"boyut": {"type": "string", "enum": ["E-I", "S-N", "T-F", "J-P"]},
                         "kutup": {"type": "string", "enum": list("EISNTFJP")},
                         "gerekce": {"type": "string", "minLength": 3}}}


@pytest.mark.model_quality
@pytest.mark.parametrize("prompt,expected", [
    ("Şu cümlede kaç kelime var: 'Bugün hava çok güzel.' Yanıtı yalnızca rakamla ver.", "4"),
    ("'kitap' kelimesinin çoğul hâlini yaz. Yalnızca kelimeyi yaz.", "kitaplar"),
])
def test_02b_turkish_with_thinking(it, prompt, expected):
    """Aynı sorular, modelin düşünme modu açıkken (analiz katmanı için yapılandırma kararı)."""
    r = _gen(it, prompt, think=True, options={"num_predict": 2048},
             system="Sen Türkçe yanıt veren, talimatlara harfiyen uyan bir asistansın.")
    print(f"\n[think] {prompt!r} -> {r.text!r} ({r.eval_count} belirteç, "
          f"düşünme {len(r.thinking)} karakter)")
    assert r.text.strip().strip(".").lower() == expected.lower()


def test_03_structured_json_output(it):
    r = _gen(it, "Aşağıdaki envanter maddesi hangi boyutla ve hangi kutupla ilgilidir? "
                 "Boyutlar: E-I (Dışadönüklük/İçedönüklük), S-N (Duyumsama/Sezgi), "
                 "T-F (Düşünme/Hissetme), J-P (Yargılama/Algılama).\n"
                 "Madde: 'Kalabalık partilerde insanlarla tanışmak bana enerji verir.'",
             schema=SCHEMA)
    print("\n", r.data)
    assert r.data["boyut"] == "E-I" and r.data["kutup"] == "E"


def _long_doc(n=60):
    import random
    rnd = random.Random(7)
    konular = ["toplantılar", "yeni fikirler", "planlı çalışma", "duygusal kararlar",
               "ayrıntılar", "yalnız kalmak", "son dakika değişiklikleri", "mantıksal analiz"]
    paras = []
    for i in range(1, n + 1):
        k = rnd.choice(konular)
        paras.append(f"Madde {i}: Kişi {k} konusunda kendini nasıl hisseder? "
                     f"Bu maddeye verilen yanıt değerlendirmede dikkate alınır. "
                     f"Açıklama: katılımcı {k} hakkında beş seçenekten birini işaretler.")
    return "\n\n".join(paras)


def test_04_long_text_chunked_analysis(it):
    text = _long_doc(60)
    chunks = chunk_text(text, max_chars=2500)
    schema = {"type": "object", "required": ["maddeler"], "additionalProperties": False,
              "properties": {"maddeler": {"type": "array", "items": {"type": "integer",
                                                                     "minimum": 1}}}}
    found: set[int] = set()
    t = time.time()
    for c in chunks:
        r = _gen(it, "Aşağıdaki metinde geçen TÜM madde numaralarını ('Madde N:' biçimindeki N "
                     "değerlerini) eksiksiz listele.\n\n" + c.text, schema=schema,
                 options={"num_predict": 400})
        found.update(r.data["maddeler"])
    print(f"\n{len(text)} karakter, {len(chunks)} parça, {time.time() - t:.1f} sn")
    assert found == set(range(1, 61))


def test_05_missing_model_error(it):
    with pytest.raises(ModelNotFoundError):
        it["client"].generate("olmayan-model:1b", "merhaba")


def test_06_port_conflict_does_not_disturb_running_runtime(it):
    pid = it["mgr"].status().pid
    other = RuntimeManager(it["cfg"], resolve_paths("linux", {"LAI_HOME": str(WORK / "diger")}, WORK),
                           base_env={"PATH": os.environ["PATH"]})
    with pytest.raises(PortConflictError):
        other.start(timeout=10)
    assert other.stop() is False
    assert it["mgr"].status().pid == pid
    assert it["client"].version()
    # sistem portu da asla seçilemez
    with pytest.raises(ValueError):
        RuntimeConfig(port=11434)


def test_07_network_audit_during_inference(it):
    pid = it["mgr"].status().pid
    with NetworkMonitor(pid, interval=0.02) as mon:
        _gen(it, "Türkiye'nin başkenti neresidir? Tek kelimeyle yanıtla.")
        _gen(it, "Bir envanter maddesini değerlendirirken nelere dikkat edilir? Kısa yanıtla.",
             options={"num_predict": 60})
    print(f"\n{mon.samples} örnek, {len(mon.seen)} soket:")
    for c in sorted(mon.seen, key=str):
        print("  ", c)
    assert mon.samples > 10
    assert any(c.state == "LISTEN" and c.local == ("127.0.0.1", it["cfg"].port) for c in mon.seen)
    assert mon.violations() == []


NETNS_SCRIPT = textwrap.dedent("""
    import json, os, socket, subprocess, sys, time
    sys.path.insert(0, {src!r})
    from lai_runtime.config import RuntimeConfig
    from lai_runtime.process import RuntimeManager
    from lai_setup.paths import resolve_paths
    from pathlib import Path
    from dataclasses import replace
    subprocess.run(["ip", "link", "set", "lo", "up"], check=True)
    out = {{"interfaces": subprocess.run(["ip", "-br", "addr"], capture_output=True,
                                          text=True).stdout.split("\\n")}}
    try:
        socket.getaddrinfo("ollama.com", 443)
        out["dns"] = "çözüldü"
    except OSError as e:
        out["dns"] = f"başarısız: {{e}}"
    try:
        socket.create_connection(("1.1.1.1", 443), timeout=3)
        out["internet"] = "bağlandı"
    except OSError as e:
        out["internet"] = f"başarısız: {{e}}"
    p = replace(resolve_paths("linux", {{"LAI_HOME": {home!r}}}, Path("/tmp")),
                runtime=Path({rt!r}))
    m = RuntimeManager(RuntimeConfig(port={port}, model={model!r}, binary={binary!r}), p,
                       base_env={{"PATH": os.environ["PATH"]}})
    m.start(timeout=60)
    try:
        r = m.client().generate({model!r}, "Yalnızca TAMAM yaz.",
                                options={{"temperature": 0, "seed": 1, "num_predict": 16}})
        out["response"] = r.text
    finally:
        m.stop()
    print(json.dumps(out, ensure_ascii=False))
""")


def test_08_inference_without_network_namespace(it, tmp_path):
    if not shutil.which("unshare"):
        pytest.fail("unshare yok: ağsız ad alanı testi yapılamıyor")
    script = tmp_path / "netns.py"
    script.write_text(NETNS_SCRIPT.format(src=str(REPO / "src"), home=str(WORK / "lai"),
                                          rt=str(WORK / "rt-netns"),
                                          port=free_port(), model=TARGET, binary=BINARY))
    # -n: yalnız loopback olan ağ ad alanı. -m + tmpfs: systemd-resolved'ın Unix soketini
    # maskeler; aksi halde glibc nss-resolve DNS sorgusunu ad alanı dışına iletir.
    r = subprocess.run(["unshare", "-rnm", "sh", "-c",
                        'mount -t tmpfs none /run/systemd/resolve 2>/dev/null; exec "$0" "$1"',
                        sys.executable, str(script)], capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stderr[-2000:]
    out = json.loads(r.stdout.strip().splitlines()[-1])
    print("\n", json.dumps(out, ensure_ascii=False, indent=1))
    assert out["dns"].startswith("başarısız") and out["internet"].startswith("başarısız")
    assert all("lo" in line or not line.strip() for line in out["interfaces"])
    assert "TAMAM" in out["response"].upper()


def _own_runtime_dir(paths, name):
    """Aynı model deposu, ayrı süreç kaydı: oturum sürecine dokunmaz."""
    return replace(paths, runtime=WORK / name)


def test_09_start_stop_restart(it):
    paths = _own_runtime_dir(it["paths"], "rt-yasam")
    m = RuntimeManager(replace(it["cfg"], port=free_port()), paths,
                       base_env={"PATH": os.environ["PATH"]})
    a = m.start(timeout=60)
    assert a.running and a.version
    b = m.restart(timeout=60)
    assert b.pid != a.pid
    assert not Path(f"/proc/{a.pid}").exists() or "Z" in Path(f"/proc/{a.pid}/stat").read_text()
    assert m.stop() is True
    time.sleep(0.5)
    assert not port_in_use("127.0.0.1", m.cfg.port)
    assert m.status().running is False
    assert it["client"].version()  # oturum süreci etkilenmedi


def test_10_cpu_fallback(it):
    it["mgr"].stop()  # bellek baskısını azalt: GPU'daki oturum süreci kapatılır
    m = RuntimeManager(replace(it["cfg"], port=free_port(), accel="cpu"),
                       _own_runtime_dir(it["paths"], "rt-cpu"),
                       base_env={"PATH": os.environ["PATH"]})
    m.start(timeout=60)
    try:
        c = m.client()
        t = time.time()
        r = c.generate(TARGET, "Yalnızca TAMAM yaz.",
                       options={"temperature": 0, "seed": 1, "num_predict": 8, "num_gpu": 0,
                                "num_ctx": 512})
        ps = c.ps()
        print(f"\nCPU yanıt: {r.text!r} ({time.time() - t:.1f} sn), ps: "
              f"{[(x['name'], x.get('size_vram')) for x in ps]}")
        assert "TAMAM" in r.text.upper()
        assert ps and all(x.get("size_vram", 0) == 0 for x in ps)
    finally:
        m.stop()
    it["mgr"].start(timeout=60)


def test_11_system_ollama_untouched(it):
    after = _system_snapshot()
    before = it["before"]
    assert after["service"] == before["service"], (before["service"], after["service"])
    assert after["tags"] == before["tags"]
    assert after["files"] == before["files"]
    print("\nsistem servisi:", after["service"].replace("\n", ", "), "| modeller:", after["tags"])


def test_12_source_model_digest_matches(it):
    src = find_source_model(SRC_DIR, it["info"]["source_model"])
    imported = find_source_model(it["paths"].models, TARGET)
    assert imported.gguf_digest == src.gguf_digest
    with pytest.raises(ModelSourceError):
        find_source_model(it["paths"].models, "olmayan:1b")
    h = hashlib.sha256()
    with open(imported.gguf, "rb") as f:
        while b := f.read(1 << 24):
            h.update(b)
    assert "sha256:" + h.hexdigest() == src.gguf_digest
