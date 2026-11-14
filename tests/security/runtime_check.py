"""Gerçek çekirdek yalıtımı içinde sahte Ollama yaşam döngüsü; model kalitesi değil."""

import json
import shutil
import socket
import subprocess
import sys
from pathlib import Path

from lai_runtime.config import RuntimeConfig
from lai_runtime.ports import PortConflictError
from lai_runtime.process import RuntimeManager
from lai_runtime.sandbox import require_isolation
from lai_setup.paths import resolve_paths


def main():
    work = Path(sys.argv[1])
    config = (
        Path(sys.argv[2])
        if len(sys.argv) > 2 and not sys.argv[2].startswith("--")
        else None
    )
    checks = require_isolation()
    paths = resolve_paths("linux", {"LAI_HOME": str(work / "runtime-check")}, work)
    paths.runtime.mkdir(parents=True, exist_ok=True)
    paths.models.mkdir(parents=True, exist_ok=True)
    if "--real-infer" in sys.argv:
        import time

        from lai_runtime.models import find_source_model, import_model

        source = find_source_model(
            Path(sys.argv[sys.argv.index("--source") + 1]),
            sys.argv[sys.argv.index("--model") + 1],
        )
        before = (source.gguf.stat().st_size, source.gguf.stat().st_mtime_ns)
        with socket.socket() as available:
            available.bind(("127.0.0.1", 0))
            port = available.getsockname()[1]
        mgr = RuntimeManager(
            RuntimeConfig(port=port, accel="cpu"), paths, binary=shutil.which("ollama")
        )
        try:
            status = mgr.start(timeout=30)
            started = time.perf_counter()
            digest = import_model(
                mgr.client(timeout=180), source, "m11-synthetic:latest"
            )
            imported = time.perf_counter() - started
            started = time.perf_counter()
            result = mgr.client(timeout=180).generate(
                "m11-synthetic:latest",
                "Yalnız TAMAM yaz. Bu sentetik bir bağlantı testidir.",
                think=False,
                options={
                    "num_ctx": 512,
                    "num_predict": 16,
                    "temperature": 0,
                    "num_thread": 4,
                },
            )
            elapsed = time.perf_counter() - started
            assert result.text.strip() or result.thinking.strip()
            assert before == (
                source.gguf.stat().st_size,
                source.gguf.stat().st_mtime_ns,
            )
            evidence = {
                "kernel": checks,
                "real_runtime_started": True,
                "version": status.version,
                "source_digest": digest,
                "source_unchanged": True,
                "imported_seconds": imported,
                "inference_seconds": elapsed,
                "real_inference": True,
                "response": result.text,
                "thinking_present": bool(result.thinking),
                "eval_count": result.eval_count,
                "cpu": True,
                "model_quality_test": False,
            }
            (work / "real-inference.json").write_text(
                json.dumps(evidence, ensure_ascii=False, indent=2)
            )
            print(json.dumps(evidence, ensure_ascii=False))
        finally:
            mgr.stop()
        return
    if "--real-start" in sys.argv:
        with socket.socket() as available:
            available.bind(("127.0.0.1", 0))
            port = available.getsockname()[1]
        binary = shutil.which("ollama")
        if not binary:
            raise RuntimeError("Mevcut yerel ikili bulunamadı; indirme yapılmadı")
        mgr = RuntimeManager(
            RuntimeConfig(port=port, accel="cpu"), paths, binary=binary
        )
        try:
            status = mgr.start(timeout=30)
            models = mgr.client().list_models()
            assert status.running and not models
            print(
                json.dumps(
                    {
                        "kernel": checks,
                        "real_runtime_started": True,
                        "version": status.version,
                        "model_count": len(models),
                        "real_inference": False,
                        "model_quality_test": False,
                    }
                )
            )
        finally:
            mgr.stop()
        return
    (paths.models / "fake-models.json").write_text(
        json.dumps({"synthetic:1": {"digest": "sha256:" + "0" * 64}})
    )
    fake = Path(__file__).resolve().parents[1] / "runtime/fake_ollama.py"
    binary = work / "fake-ollama"
    extra = ""
    if config:
        probe = Path(__file__).with_name("probe.py")
        extra = f"{sys.executable} {probe} {config} > {work / 'ollama-probe.json'}\n"
    binary.write_text(f'#!/bin/sh\n{extra}exec "{sys.executable}" "{fake}" "$@"\n')
    binary.chmod(0o700)
    with socket.socket() as foreign:
        foreign.bind(("127.0.0.1", 0))
        foreign.listen()
        port = foreign.getsockname()[1]
        mgr = RuntimeManager(RuntimeConfig(port=port), paths, binary=str(binary))
        try:
            mgr.start(timeout=3)
            raise AssertionError("Port çakışması reddedilmedi")
        except PortConflictError:
            pass
        assert foreign.fileno() >= 0
        assert not mgr.record_file.exists()
        assert not (work / "ollama-probe.json").exists()
    with socket.socket() as available:
        available.bind(("127.0.0.1", 0))
        port = available.getsockname()[1]
    mgr = RuntimeManager(RuntimeConfig(port=port), paths, binary=str(binary))
    try:
        result = mgr.start(timeout=10)
        assert result.running and result.owned
        generation = mgr.client().generate(
            "synthetic:1", "Yalnız sentetik bağlantı testi"
        )
        assert generation.text
        if config:
            probes = json.loads((work / "ollama-probe.json").read_text())
            assert not any(probes.values()), probes
        # Yabancı bir alt süreç durdurma sırasında korunmalı.
        foreign = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"]
        )
        try:
            assert mgr.stop()
            assert foreign.poll() is None
        finally:
            foreign.terminate()
            foreign.wait(5)
        assert not mgr.status().running and not mgr.record_file.exists()
        print(
            json.dumps(
                {
                    "kernel": checks,
                    "fake_inference": True,
                    "model_quality_test": False,
                    "port_conflict_preserved": True,
                    "foreign_process_preserved": True,
                    "owned_stopped": True,
                    "ollama_probes": json.loads(
                        (work / "ollama-probe.json").read_text()
                    )
                    if config
                    else None,
                }
            )
        )
    finally:
        mgr.stop()


if __name__ == "__main__":
    main()
