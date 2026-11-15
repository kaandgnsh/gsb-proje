"""Kurulumsuz deneme modu: ayrı kökte, mevcut yerel modelle, yalıtım içinde tanı ve arayüz.

Sisteme kurulum yapmaz, başlatıcı/kısayol eklemez, model indirmez. Modeli başka bir Ollama
deposundan salt okunur kopyalar; sistem Ollama servisine istek göndermez.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

MARKER = ".lai-deneme"
DEFAULT_ROOT = Path.home() / ".cache" / "local-assessment-ai" / "deneme"
SOURCES = (Path("/var/lib/ollama"), Path.home() / ".ollama" / "models")
PROMPT = "Yalnızca TAMAM yaz."


def _source_models(store: Path) -> list[str]:
    from lai_runtime.models import list_source_models

    return list_source_models(store)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _paths(home: Path):
    from .paths import resolve_paths

    return resolve_paths("linux", {"LAI_HOME": str(home)}, Path.home())


def _write_config(paths, model: str) -> None:
    paths.config_dir.mkdir(parents=True, exist_ok=True)
    paths.config_file.write_text(
        f'[ollama]\nmodel = {json.dumps(model)}\nport = 11435\n', encoding="utf-8"
    )


def _import_model(paths, store: Path, model: str, out) -> None:
    from lai_runtime.config import RuntimeConfig
    from lai_runtime.models import find_source_model, import_model
    from lai_runtime.process import RuntimeManager

    from .m2_steps import model_installed

    if model_installed(paths, model):
        out(f"Model deneme kökünde hazır: {model}")
        return
    source = find_source_model(store, model)
    size = source.gguf.stat().st_size
    free = shutil.disk_usage(paths.root).free
    if free < size * 1.2:
        raise RuntimeError(
            f"Deneme kökünde yer yetersiz: model {size >> 20} MB, boş {free >> 20} MB"
        )
    out(f"Model salt okunur kopyalanıyor: {model} ({size >> 20} MB) — {store}")
    mgr = RuntimeManager(
        RuntimeConfig(port=_free_port(), model=model),
        paths,
        base_env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
    )
    mgr.start(timeout=60)
    try:
        import_model(mgr.client(), source, model)
    finally:
        mgr.stop()
    out("Model kopyalandı.")


def diagnose() -> int:
    """Yalıtım içinde çalışır: yalıtım, veritabanı, model süreci ve kısa çıkarım."""
    from lai_runtime.config import load_runtime_config
    from lai_runtime.process import RuntimeManager
    from lai_runtime.sandbox import kernel_status

    from .paths import resolve_paths

    paths = resolve_paths("linux", os.environ, Path.home())
    report: dict = {"isolation": kernel_status()}
    ok = all(report["isolation"].values())
    try:
        socket.create_connection(("1.1.1.1", 443), timeout=2).close()
        report["internet_blocked"] = False
        ok = False
    except OSError:
        report["internet_blocked"] = True
    try:
        from lai.store.store import Store

        paths.data.mkdir(parents=True, exist_ok=True)
        store = Store.open(paths.data / "lai.db", backup_dir=paths.data / "backups")
        report["database"] = {"ok": True, "path": str(paths.data / "lai.db")}
        store.close()
    except Exception as error:  # noqa: BLE001 — tanı raporu her hatayı göstermeli
        report["database"] = {"ok": False, "error": f"{type(error).__name__}: {error}"}
        ok = False
    cfg = load_runtime_config(paths)
    mgr = RuntimeManager(cfg, paths)
    rt: dict = {"model": cfg.model, "binary": mgr.resolve_binary()}
    try:
        t = time.time()
        status = mgr.start(timeout=60)
        rt["ollama_version"] = status.version
        rt["start_seconds"] = round(time.time() - t, 1)
        client = mgr.client()
        names = [m.get("name", "") for m in client.list_models()]
        rt["model_present"] = cfg.model in names
        if rt["model_present"]:
            t = time.time()
            result = client.generate(
                cfg.model, PROMPT, options={"temperature": 0, "seed": 42}
            )
            rt["generate_seconds"] = round(time.time() - t, 1)
            rt["answer"] = result.text.strip()[:200]
            rt["inference_ok"] = bool(result.text.strip())
        else:
            rt["inference_ok"] = False
        ok = ok and rt["inference_ok"]
    except Exception as error:  # noqa: BLE001
        rt["error"] = f"{type(error).__name__}: {error}"
        ok = False
    finally:
        mgr.stop()
    report["runtime"] = rt
    report["ok"] = ok
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if ok else 1


def _launch(root: Path, home: Path, args: list[str], log: Path, *, gui: bool):
    from lai_runtime.sandbox import launch

    with open(log, "a", encoding="utf-8") as fh:
        fh.write(f"\n=== {time.strftime('%F %T')} {' '.join(args) or 'arayüz'} ===\n")
        fh.flush()
        return launch(
            [str(root / "AppRun"), *args],
            writable=[home],
            readable=[root],
            gui=gui,
            env={**os.environ, "LAI_HOME": str(home)},
            stdout=fh if gui else subprocess.PIPE,
            stderr=fh,
            text=True,
        )


def main(root: Path, argv: list[str]) -> int:
    p = argparse.ArgumentParser(
        prog="--deneme",
        description="Kurulumsuz deneme: ayrı kökte mevcut yerel modelle tanı ve arayüz",
    )
    p.add_argument("--kok", type=Path, default=DEFAULT_ROOT, help="deneme kökü")
    p.add_argument("--kaynak", type=Path, help="salt okunur Ollama model deposu")
    p.add_argument("--model", help="kaynak depodaki model adı (vars. ilk model)")
    p.add_argument("--modelsiz", action="store_true", help="model kopyalama ve çıkarım yok")
    p.add_argument("--yalniz-tani", action="store_true", help="arayüzü açma")
    p.add_argument("--temizle", action="store_true", help="deneme kökünü sil")
    a = p.parse_args(argv)
    home = a.kok.expanduser().absolute()
    out = print

    if a.temizle:
        if not (home / MARKER).is_file():
            out(f"{home} bir deneme kökü değil ({MARKER} yok); silinmedi.", file=sys.stderr)
            return 2
        shutil.rmtree(home)
        out(f"Deneme kökü silindi: {home}")
        return 0

    if home.exists() and any(home.iterdir()) and not (home / MARKER).is_file():
        out(f"{home} boş değil ve deneme kökü değil; kullanılmadı.", file=sys.stderr)
        return 2
    home.mkdir(parents=True, exist_ok=True)
    (home / MARKER).write_text("Kurulumsuz deneme kökü; --deneme --temizle siler.\n")
    paths = _paths(home)
    paths.logs.mkdir(parents=True, exist_ok=True)
    log = paths.logs / f"deneme-{time.strftime('%Y%m%d-%H%M%S')}.log"

    model = a.model
    if not a.modelsiz:
        store = a.kaynak or next(
            (s for s in SOURCES if os.access(s, os.R_OK) and _source_models(s)), None
        )
        if store is None:
            out("Okunabilir Ollama model deposu yok; --kaynak verin ya da --modelsiz kullanın.",
                file=sys.stderr)
            return 2
        available = _source_models(store)
        model = model or (available[0] if available else None)
        if model not in available:
            out(f"{store} içinde model yok: {model}. Mevcut: {', '.join(available) or '-'}",
                file=sys.stderr)
            return 2
        try:
            _import_model(paths, store, model, out)
        except (OSError, RuntimeError) as error:
            out(f"Model kopyalanamadı: {error}", file=sys.stderr)
            return 2
    _write_config(paths, model or "qwen3.5:4b")

    out(f"Deneme kökü: {home}\nGünlük: {log}\nYalıtım içinde tanı çalışıyor...")
    try:
        result = _launch(root, home, ["--tani"], log, gui=False)
    except RuntimeError as error:
        out(f"Yalıtım başlatılamadı: {error}", file=sys.stderr)
        return 2
    text = (result.stdout or "").strip()
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(text + "\n")
    try:
        report = json.loads(text)
    except ValueError:
        out(f"Tanı çıktısı okunamadı (çıkış {result.returncode}); günlüğe bakın: {log}",
            file=sys.stderr)
        return 1
    rt = report.get("runtime", {})
    lines = [
        ("Ağ yalıtımı", all(report["isolation"].values()) and report["internet_blocked"]),
        ("Veritabanı", report["database"]["ok"]),
        ("Uygulamanın Ollama süreci", "ollama_version" in rt),
    ]
    if not a.modelsiz:
        lines.append((f"Model çıkarımı ({rt.get('model')})", rt.get("inference_ok", False)))
    for title, good in lines:
        out(f"  [{'TAMAM' if good else 'HATA '}] {title}")
    if rt.get("error"):
        out(f"  Ayrıntı: {rt['error']}")
    if "generate_seconds" in rt:
        out(f"  Yanıt: {rt.get('answer')!r} ({rt['generate_seconds']} sn)")
    healthy = all(good for _, good in lines)
    if a.yalniz_tani:
        return 0 if healthy else 1
    if not healthy:
        out("Tanıda hata var; arayüz yine de açılıyor (sorunu arayüzde görmek için).")
    out("Arayüz açılıyor; kapandığında günlük yolu yukarıda.")
    try:
        gui = _launch(root, home, [], log, gui=True)
    except RuntimeError as error:
        out(f"Arayüz açılamadı: {error}", file=sys.stderr)
        return 2
    if gui.returncode:
        out(f"Arayüz {gui.returncode} koduyla kapandı. Günlük sonu:", file=sys.stderr)
        out("".join(log.read_text(encoding="utf-8").splitlines(True)[-30:]), file=sys.stderr)
    return gui.returncode
