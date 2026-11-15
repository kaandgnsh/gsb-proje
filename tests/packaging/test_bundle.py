import json
from pathlib import Path

import pytest

from lai_setup.bundle import digest, manage, verify


def bundle(path):
    import platform

    path.mkdir()
    (path / "AppRun").write_text("synthetic")
    (path / "AppRun").chmod(0o755)
    (path / "package.json").write_text(
        json.dumps(
            {
                "version": "test",
                "architecture": platform.machine(),
                "files": {"AppRun": digest(path / "AppRun")},
            }
        )
    )
    return path


def test_corrupt_before_install(tmp_path):
    source = bundle(tmp_path / "bundle")
    (source / "AppRun").write_text("broken")
    with pytest.raises(ValueError, match="bütünlüğü"):
        manage("install", tmp_path / "home", source)
    assert not (tmp_path / "home").exists()


def test_lifecycle_preserves_database_and_reports(tmp_path):
    source = bundle(tmp_path / "bundle")
    home = tmp_path / "home"
    first = manage("install", home, source)
    assert manage("install", home, source)["release"] == first["release"]
    report = home / "reports/eski.json"
    report.write_text('{"old":true}')
    import sqlite3

    with sqlite3.connect(home / "data/lai.db") as db:
        db.execute("CREATE TABLE synthetic_history(value)")
        db.execute("INSERT INTO synthetic_history VALUES (42)")
    from lai.assessment.service import submit_single
    from tests.assessment.conftest import build_store

    store, _, iv, _, pv = build_store(home / "data/lai.db")
    result = submit_single(
        store,
        inventory_version_id=iv,
        profile_version_id=pv,
        participant_code="SYN-UPDATE",
        responses=[(i, "A") for i in range(1, 9)],
    )
    assessment = store.assessments.get(result["assessment_id"])
    profile = store.profiles.get_version(pv)
    store.close()
    (Path(first["release"]) / "AppRun").chmod(0o600)
    with pytest.raises(ValueError, match="çalıştırma izni"):
        verify(Path(first["release"]))
    manage("repair", home, source)
    assert (Path(first["release"]) / "AppRun").stat().st_mode & 0o111
    (Path(first["release"]) / "AppRun").write_text("damaged")
    manage("repair", home, source)
    assert manage("doctor", home)["ok"]
    (source / "AppRun").write_text("new synthetic version")
    m = json.loads((source / "package.json").read_text())
    m["version"] = "new"
    m["files"]["AppRun"] = digest(source / "AppRun")
    (source / "package.json").write_text(json.dumps(m))
    manage("install", home, source)
    with sqlite3.connect(home / "data/lai.db") as db:
        assert db.execute("SELECT value FROM synthetic_history").fetchone()[0] == 42
    from lai.store.store import Store

    store = Store.open(home / "data/lai.db")
    assert store.assessments.get(assessment.id) == assessment
    assert store.profiles.get_version(pv) == profile
    store.close()
    manage("uninstall", home)
    assert report.exists() and (home / "data/lai.db").exists()
    assert not (home / "app").exists()


def test_symlink_rejected(tmp_path):
    source = bundle(tmp_path / "bundle")
    external = tmp_path / "outside"
    external.write_text("synthetic")
    (source / "AppRun").unlink()
    (source / "AppRun").symlink_to(external)
    with pytest.raises(ValueError):
        verify(source)


def test_packaged_probe_does_not_require_wayland(tmp_path):
    import os
    import platform
    import shlex
    import subprocess
    import sys

    root = tmp_path / "app"
    root.mkdir()
    source = Path(__file__).resolve().parents[2] / "src"
    run = root / "AppRun"
    run.write_text(
        "#!/bin/sh\nexport PYTHONPATH="
        + shlex.quote(str(source))
        + "\nexec "
        + shlex.quote(sys.executable)
        + " -m lai_setup.package_entry "
        + shlex.quote(str(root))
        + ' "$@"\n'
    )
    run.chmod(0o755)
    (root / "package.json").write_text(
        json.dumps(
            {"architecture": platform.machine(), "files": {"AppRun": digest(run)}}
        )
    )
    targets = tmp_path / "targets.json"
    targets.write_text('{"targets":{}}')
    result = subprocess.run(
        [str(run), "--probe", str(targets)],
        env={
            **os.environ,
            "LAI_HOME": str(tmp_path / "home"),
            "LAI_WORKSPACE": str(tmp_path),
            "XDG_RUNTIME_DIR": "/nonexistent",
        },
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"glibc_nss_resolve": False}


@pytest.mark.parametrize("kind", ["runtime", "model"])
def test_separate_local_components_preserve_existing(tmp_path, kind):
    from lai_setup.bundle import build_component

    source = tmp_path / "local"
    source.mkdir()
    if kind == "runtime":
        (source / "ollama").write_text("#!/bin/sh\nexit 0\n")
        (source / "ollama").chmod(0o755)
    else:
        (source / "blobs").mkdir()
        (source / "manifests").mkdir()
        import hashlib

        value = b"GGUF-SENTETIK"
        checksum = hashlib.sha256(value).hexdigest()
        (source / "blobs" / ("sha256-" + checksum)).write_bytes(value)
        (source / "manifests/synthetic").write_text(
            json.dumps({"layers": [{"digest": "sha256:" + checksum}]})
        )
    package = build_component(source, tmp_path / "component", kind)
    result = manage("install", tmp_path / "home", package)
    assert result["component"] == kind
    with pytest.raises(ValueError, match="Mevcut bileşen"):
        manage("install", tmp_path / "home", package)


@pytest.mark.parametrize("state", ["missing", "corrupt"])
def test_component_blob_preflight_before_output(tmp_path, state):
    import hashlib

    from lai_setup.bundle import build_component

    source = tmp_path / "models"
    (source / "blobs").mkdir(parents=True)
    (source / "manifests").mkdir()
    checksum = hashlib.sha256(b"GGUF-SENTETIK").hexdigest()
    (source / "manifests/synthetic").write_text(
        json.dumps({"layers": [{"digest": "sha256:" + checksum}]})
    )
    if state == "corrupt":
        (source / "blobs" / ("sha256-" + checksum)).write_bytes(b"broken")
    with pytest.raises(ValueError, match="eksik veya bozuk"):
        build_component(source, tmp_path / "output", "model")
    assert not (tmp_path / "output").exists()


def test_self_install_and_repair_select_embedded_package(tmp_path, monkeypatch):
    import sys

    from lai_setup import package_entry

    source = bundle(tmp_path / "self")
    seen = []
    monkeypatch.setattr("lai_setup.cli.main", lambda args: seen.append(args) or 0)
    for action in ("install", "repair"):
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "AppRun",
                str(source),
                "--setup",
                action,
                "--lai-home",
                str(tmp_path / "home"),
            ],
        )
        assert package_entry.main() == 0
        assert seen[-1][-2:] == ["--bundle", str(source)]


def test_maintenance_cannot_start_unisolated_runtime(tmp_path, monkeypatch):
    import sys

    from lai_setup import package_entry

    source = bundle(tmp_path / "self")
    monkeypatch.setattr(
        sys, "argv", ["AppRun", str(source), "--setup", "runtime", "run"]
    )
    assert package_entry.main() == 2


def test_bundle_dry_run_does_not_install(tmp_path, capsys):
    from lai_setup.cli import main

    source = bundle(tmp_path / "source")
    target = tmp_path / "never-created"
    assert (
        main(
            ["install", "--bundle", str(source), "--lai-home", str(target), "--dry-run"]
        )
        == 0
    )
    assert not target.exists()
    assert json.loads(capsys.readouterr().out)["dry_run"]


def test_missing_entrypoint_rejected_before_install(tmp_path):
    import platform

    source = tmp_path / "empty"
    source.mkdir()
    for kind in ("application", "runtime", "model"):
        (source / "package.json").write_text(
            json.dumps(
                {
                    "kind": kind,
                    "version": "test",
                    "architecture": platform.machine(),
                    "files": {},
                }
            )
        )
        with pytest.raises(ValueError):
            manage("install", tmp_path / "home", source)
        assert not (tmp_path / "home").exists()
