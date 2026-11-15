"""Yerel dosyalardan çevrimdışı AppDir; doğrulanmış kurulum ve güncelleme."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

VERSION = "0.12.0"


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def verify(root):
    root = Path(root).resolve()
    manifest = json.loads((root / "package.json").read_text())
    if (
        not isinstance(manifest, dict)
        or not isinstance(manifest.get("files"), dict)
        or not isinstance(manifest.get("architecture"), str)
    ):
        raise ValueError("Paket manifest şeması geçersiz")  # noqa: TRY004 - kullanıcıya paket doğrulama hatası
    if any(p.is_symlink() for p in root.rglob("*")):
        raise ValueError("Paket sembolik bağlantı içeremez")
    if manifest["architecture"] != platform.machine():
        raise ValueError("Paket mimarisi bu bilgisayarla eşleşmiyor")
    kind = manifest.get("kind", "application")
    if kind not in ("application", "runtime", "model"):
        raise ValueError("Bilinmeyen paket bileşeni")
    if kind == "application" and "AppRun" not in manifest["files"]:
        raise ValueError("Paket başlangıç dosyası eksik: AppRun")
    if kind == "application" and not (root / "AppRun").stat().st_mode & 0o111:
        raise ValueError("Paket başlangıç dosyasının çalıştırma izni eksik")
    if kind == "runtime" and not (root / "payload/ollama").is_file():
        raise ValueError("Çalışma ortamı paket dosyası eksik")
    if kind == "model" and (
        not (root / "payload/blobs").is_dir()
        or not any(
            (root / "payload" / x).is_dir() for x in ("manifests", "manifests-v2")
        )
    ):
        raise ValueError("Model paket dosyaları eksik")
    actual = {
        str(p.relative_to(root))
        for p in root.rglob("*")
        if p.is_file() and p != root / "package.json"
    }
    if actual != set(manifest["files"]):
        raise ValueError("Paket dosyası eksik veya beklenmeyen dosya var")
    for name, expected in manifest["files"].items():
        p = root / name
        if (
            p.is_symlink()
            or not p.resolve().is_relative_to(root)
            or digest(p) != expected
        ):
            raise ValueError("Paket bütünlüğü bozuk: " + name)
    return manifest


def build(output, *, python=sys.executable):
    output = Path(output).absolute()
    if output.exists():
        raise ValueError("Hedef zaten var; boş yeni bir paket yolu seçin")
    if Path(python).resolve() != Path(sys.executable).resolve():
        raise ValueError("Oluşturucuyu paketlenecek Python ortamıyla çalıştırın")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent, prefix=".lai-build-") as temp:
        root = Path(temp) / "AppDir"
        (root / "usr/bin").mkdir(parents=True)
        shutil.copy2(Path(python).resolve(), root / "usr/bin/python3")
        stdlib = (
            Path(sys.base_prefix)
            / "lib"
            / f"python{sys.version_info.major}.{sys.version_info.minor}"
        )
        shutil.copytree(
            stdlib,
            root / "usr/lib" / stdlib.name,
            ignore=shutil.ignore_patterns(
                "site-packages", "__pycache__", "test", "tests", "*.pyc"
            ),
        )
        site = next(
            p for p in sys.path if p.endswith("site-packages") and Path(p).is_dir()
        )
        shutil.copytree(
            site,
            root / "site-packages",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        source = Path(__file__).resolve().parents[1]
        for name in ("lai", "lai_runtime", "lai_setup"):
            shutil.copytree(
                source / name,
                root / "src" / name,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
        from PIL import Image, ImageDraw

        icon = Image.new("RGB", (64, 64), "#26384a")
        ImageDraw.Draw(icon).text((25, 20), "E", fill="white")
        icon.save(root / "lai.png")
        shutil.copy2(root / "lai.png", root / ".DirIcon")
        run = root / "AppRun"
        run.write_text("""#!/bin/sh
set -eu
APPDIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
export PYTHONHOME="$APPDIR/usr"
export PYTHONPATH="$APPDIR/src:$APPDIR/site-packages"
export PYTHONDONTWRITEBYTECODE=1
exec "$APPDIR/usr/bin/python3" -m lai_setup.package_entry "$APPDIR" "$@"
""")
        run.chmod(0o755)
        (root / "lai.desktop").write_text(
            "[Desktop Entry]\nType=Application\nName=Yerel Envanter\nExec=AppRun\nIcon=lai\nCategories=Education;\nTerminal=true\n"
        )
        (root / "lai.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"><rect width="64" height="64" rx="12" fill="#26384a"/><text x="12" y="44" fill="white" font-size="34">E</text></svg>'
        )
        dependencies = sorted(
            f"{d.metadata['Name']}=={d.version}"
            for d in importlib.metadata.distributions(path=[site])
        )
        manifest = {
            "version": VERSION,
            "architecture": platform.machine(),
            "python": platform.python_version(),
            "libc": platform.libc_ver(),
            "dependencies": dependencies,
            "system_dependencies": ["glibc", "bubblewrap", "libseccomp", "Wayland"],
            "model_included": False,
            "runtime_included": False,
            "files": {
                str(p.relative_to(root)): digest(p)
                for p in root.rglob("*")
                if p.is_file()
            },
        }
        (root / "package.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2)
        )
        verify(root)
        root.rename(output)
    return output


def build_component(source, output, kind):
    if kind not in ("runtime", "model"):
        raise ValueError("Geçersiz bileşen")
    source, output = Path(source), Path(output)
    if output.exists() or not source.is_dir():
        raise ValueError("Kaynak dizin olmalı; hedef yeni olmalı")
    if kind == "runtime" and not (source / "ollama").is_file():
        raise ValueError("Yerel çalışma ortamında ollama dosyası eksik")
    if kind == "model" and (
        not (source / "blobs").is_dir()
        or not any((source / x).is_dir() for x in ("manifests", "manifests-v2"))
    ):
        raise ValueError("Yerel model deposunda blobs/manifest eksik")
    if kind == "runtime" and not os.access(source / "ollama", os.X_OK):
        raise ValueError("Çalışma ortamı ikilisi çalıştırılabilir değil")
    if kind == "model":
        manifests = [
            p
            for folder in ("manifests", "manifests-v2")
            for p in (source / folder).rglob("*")
            if p.is_file()
        ]
        if not manifests:
            raise ValueError("Model manifesti bulunamadı")
        for record in manifests:
            layers = json.loads(record.read_text()).get("layers", [])
            if not layers:
                raise ValueError("Model manifest katmanları eksik")
            for layer in layers:
                checksum = layer.get("digest", "")
                if not re.fullmatch(r"sha256:[0-9a-f]{64}", checksum):
                    raise ValueError("Geçersiz model blob özeti")
                blob = source / "blobs" / checksum.replace(":", "-")
                if not blob.is_file() or digest(blob) != checksum.split(":")[1]:
                    raise ValueError("Model blob dosyası eksik veya bozuk")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as temporary:
        staging = Path(temporary) / "component"
        shutil.copytree(source, staging / "payload")
        manifest = {
            "version": VERSION,
            "kind": kind,
            "architecture": platform.machine(),
            "files": {
                str(f.relative_to(staging)): digest(f)
                for f in staging.rglob("*")
                if f.is_file()
            },
        }
        (staging / "package.json").write_text(json.dumps(manifest, indent=2))
        verify(staging)
        staging.rename(output)
    return output


def manage(action, home, bundle=None):
    home = Path(home).expanduser().absolute()
    marker = home / ".lai-package.json"
    if action in ("install", "repair"):
        if bundle is None:
            raise ValueError("Onarım için --bundle ile sağlam yerel paket gerekli")
        manifest = verify(bundle)  # Hedefe yazmadan önce bütün paket doğrulanır.
        if manifest.get("kind") in ("runtime", "model"):
            kind = manifest["kind"]
            target = home / "runtime" / ("bin" if kind == "runtime" else "models")
            if target.exists() and any(target.iterdir()):
                raise ValueError(
                    "Mevcut bileşen korunuyor; dolu depo üzerine yazılamaz"
                )
            target.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=target.parent) as temporary:
                stage = Path(temporary) / "payload"
                shutil.copytree(Path(bundle) / "payload", stage)
                if target.exists():
                    target.rmdir()
                stage.rename(target)
            (target.parent / (kind + "-package.json")).write_text(json.dumps(manifest))
            return {
                "component": kind,
                "installed": True,
                "version": manifest["version"],
            }
        home.mkdir(parents=True, exist_ok=True)
        db = home / "data/lai.db"
        if db.exists():
            backup = home / "data/backups"
            backup.mkdir(exist_ok=True)
            with (
                sqlite3.connect(db) as source,
                sqlite3.connect(backup / (os.urandom(8).hex() + ".db")) as dest,
            ):
                source.backup(dest)
        for name in ("data", "reports", "runtime/models", "config"):
            (home / name).mkdir(parents=True, exist_ok=True)
        app = home / "app"
        app.mkdir(exist_ok=True)
        identity = digest(Path(bundle) / "package.json")
        target = app / identity
        if target.exists():
            try:
                verify(target)
            except (ValueError, OSError):
                # Yalnız bu yöneticinin hash adıyla kaydettiği bozuk sürüm karantinaya alınır.
                target.rename(app / (identity + ".damaged-" + os.urandom(4).hex()))
        if not target.exists():
            with tempfile.TemporaryDirectory(dir=app) as temp:
                staging = Path(temp) / "release"
                shutil.copytree(bundle, staging)
                verify(staging)
                staging.rename(target)
        launcher = home / "yerel-envanter"
        import shlex

        launcher.write_text(
            "#!/bin/sh\nexport LAI_HOME="
            + shlex.quote(str(home))
            + "\nexec "
            + shlex.quote(str(target / "AppRun"))
            + ' "$@"\n'
        )
        launcher.chmod(0o755)
        desktop = home / "yerel-envanter.desktop"
        escaped = (
            str(launcher)
            .replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("%", "%%")
            .replace("`", "\\`")
            .replace("$", "\\$")
        )
        desktop.write_text(
            '[Desktop Entry]\nType=Application\nName=Yerel Envanter\nTerminal=true\nExec="'
            + escaped
            + '"\n'
        )
        marker.write_text(json.dumps({"release": str(target), "manifest": manifest}))
        from lai.store.store import Store

        s = Store.open(db, backup_dir=home / "data/backups")
        s.close()
        return {
            "action": action,
            "version": manifest["version"],
            "release": str(target),
            "data_preserved": True,
        }
    if not marker.is_file():
        raise ValueError("Yönetilen paket kurulumu bulunamadı")
    state = json.loads(marker.read_text())
    release = Path(state["release"])
    if not release.resolve().is_relative_to((home / "app").resolve()):
        raise ValueError("Geçersiz kurulum sahipliği")
    if action == "doctor":
        verify(release)
        return {
            "ok": True,
            "version": state["manifest"]["version"],
            "network_enforcement": "başlangıçta zorunlu; bu denetim ağ kanıtı değildir",
        }
    if action == "uninstall":
        for candidate in (home / "app").iterdir():
            if (
                re.fullmatch(r"[0-9a-f]{64}(?:\.damaged-[0-9a-f]{8})?", candidate.name)
                and (candidate / "package.json").is_file()
            ):
                shutil.rmtree(candidate)
        if not any((home / "app").iterdir()):
            (home / "app").rmdir()
        for name in ("yerel-envanter", "yerel-envanter.desktop", ".lai-package.json"):
            (home / name).unlink(missing_ok=True)
        return {
            "removed": True,
            "preserved": ["data", "reports", "config", "runtime/models"],
        }
    raise ValueError("Bilinmeyen paket işlemi")


def main(argv=None):
    p = argparse.ArgumentParser(description="Yerel çevrimdışı paket oluşturucu")
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--appimagetool", type=Path)
    p.add_argument("--appimage-runtime", type=Path)
    p.add_argument(
        "--component",
        choices=["application", "runtime", "model"],
        default="application",
    )
    p.add_argument("--source", type=Path)
    a = p.parse_args(argv)
    if a.component != "application":
        if not a.source:
            raise ValueError("Bileşen için --source yerel dizin gerekli")
        print(build_component(a.source, a.output, a.component))
        return 0
    output = build(a.output)
    if a.appimagetool:
        if not a.appimage_runtime:
            raise ValueError(
                "Çevrimdışı AppImage üretimi için --appimage-runtime gerekli"
            )
        subprocess.run(
            [
                str(a.appimagetool),
                "--no-appstream",
                "--runtime-file",
                str(a.appimage_runtime),
                str(output),
                str(output) + ".AppImage",
            ],
            check=True,
        )
    print(output)
