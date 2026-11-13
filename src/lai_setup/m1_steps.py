"""M1 kurulum adımları: dizinler, ayarlar, venv, Python bağımlılıkları."""
from __future__ import annotations

import hashlib
import os
import time
import tomllib
from pathlib import Path

from .steps import Context, Step

DEFAULT_CONFIG = """\
# Local Assessment AI ayarları. Bu dosya kurulum tarafından bir kez yazılır;
# sonraki kurulumlar sizin değişikliklerinizi korur.

[ollama]
host = "127.0.0.1"
port = 11435
model = "qwen3.5:4b"
no_cloud = true

[app]
language = "tr"
network = "offline"
telemetry = false
update_check = false
"""


def venv_python(venv: Path, platform: str) -> Path:
    return venv / ("Scripts/python.exe" if platform == "windows" else "bin/python")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class EnsureDirectories(Step):
    id = "dirs"
    title = "Uygulama dizinleri"

    def check(self, ctx: Context) -> bool:
        return self.verify(ctx)[0]

    def apply(self, ctx: Context) -> None:
        for d in ctx.paths.all_dirs():
            d.mkdir(parents=True, exist_ok=True)
        for d in ctx.paths.user_data_dirs():
            os.chmod(d, 0o700)

    def verify(self, ctx: Context) -> tuple[bool, str]:
        missing = [str(d) for d in ctx.paths.all_dirs() if not d.is_dir()]
        if missing:
            return False, "eksik dizin: " + ", ".join(missing)
        if ctx.info.os != "windows":
            loose = [str(d) for d in ctx.paths.user_data_dirs() if d.stat().st_mode & 0o077]
            if loose:
                return False, "izinler fazla açık: " + ", ".join(loose)
        return True, str(ctx.paths.root)


class WriteConfig(Step):
    id = "config"
    title = "Ayar dosyası"

    def check(self, ctx: Context) -> bool:
        return self.verify(ctx)[0]

    def apply(self, ctx: Context) -> None:
        cfg = ctx.paths.config_file
        cfg.parent.mkdir(parents=True, exist_ok=True)
        if cfg.exists():  # yalnız okunamayan dosyaya gelinir: önce yedek
            cfg.rename(cfg.with_name(f"{cfg.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}"))
        cfg.write_text(DEFAULT_CONFIG, encoding="utf-8")

    def verify(self, ctx: Context) -> tuple[bool, str]:
        cfg = ctx.paths.config_file
        if not cfg.exists():
            return False, "ayar dosyası yok"
        try:
            data = tomllib.loads(cfg.read_text(encoding="utf-8"))
        except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
            return False, f"ayar dosyası okunamıyor: {e}"
        host = data.get("ollama", {}).get("host", "127.0.0.1")
        if host not in ("127.0.0.1", "::1", "localhost"):
            return False, f"ollama.host yalnız 127.0.0.1 olabilir (şu an: {host})"
        return True, str(cfg)


class CreateVenv(Step):
    id = "venv"
    title = "Uygulama Python ortamı (venv)"

    def check(self, ctx: Context) -> bool:
        return self.verify(ctx)[0]

    def apply(self, ctx: Context) -> None:
        r = ctx.runner.run([ctx.python, "-m", "venv", "--clear", str(ctx.paths.venv)], timeout=300)
        if r.returncode != 0:
            raise RuntimeError(f"venv oluşturulamadı: {(r.stderr or r.stdout).strip()}")

    def verify(self, ctx: Context) -> tuple[bool, str]:
        py = venv_python(ctx.paths.venv, ctx.info.os)
        if not py.exists():
            return False, "venv yok"
        r = ctx.runner.run([str(py), "-c", "import sys; print(sys.version)"], timeout=30)
        return (r.returncode == 0, (r.stdout or r.stderr or "").strip())

    def details(self, ctx: Context) -> dict:
        ctx.state.pins["python"] = ctx.info.python_version
        return {}


class InstallPythonDeps(Step):
    id = "pydeps"
    title = "Python bağımlılıkları"

    def _req(self, ctx: Context) -> Path:
        if ctx.requirements_file is None:
            raise RuntimeError("bağımlılık listesi belirtilmedi")
        return ctx.requirements_file

    def check(self, ctx: Context) -> bool:
        recorded = ctx.state.steps.get(self.id, {}).get("req_sha256")
        return recorded == _sha256(self._req(ctx)) and self.verify(ctx)[0]

    def apply(self, ctx: Context) -> None:
        if ctx.mode == "none":
            raise RuntimeError("çevrimiçi ya da çevrimdışı kaynak seçilmedi "
                               "(--online veya --offline <dizin>)")
        py = str(venv_python(ctx.paths.venv, ctx.info.os))
        argv = [py, "-m", "pip", "install", "--disable-pip-version-check", "--no-input"]
        env = dict(os.environ, PIP_NO_INPUT="1", PIP_DISABLE_PIP_VERSION_CHECK="1")
        if ctx.mode == "offline":
            if ctx.offline_dir is None:
                raise RuntimeError("çevrimdışı paket dizini verilmedi")
            argv += ["--no-index", "--find-links", str(ctx.offline_dir / "wheels")]
            # kullanıcının pip.conf'undaki ek indeksler ağ erişimine yol açmasın
            env.update(PIP_CONFIG_FILE=os.devnull, PIP_NO_INDEX="1")
        argv += ["-r", str(self._req(ctx))]
        r = ctx.runner.run(argv, timeout=3600, env=env)
        if r.returncode != 0:
            raise RuntimeError(f"pip başarısız: {(r.stderr or r.stdout).strip()[-800:]}")

    def verify(self, ctx: Context) -> tuple[bool, str]:
        py = str(venv_python(ctx.paths.venv, ctx.info.os))
        r = ctx.runner.run([py, "-m", "pip", "check"], timeout=120,
                           env=dict(os.environ, PIP_CONFIG_FILE=os.devnull))
        return r.returncode == 0, (r.stdout or r.stderr or "").strip()

    def details(self, ctx: Context) -> dict:
        sha = _sha256(self._req(ctx))
        ctx.state.pins["requirements_sha256"] = sha
        return {"req_sha256": sha}


def m1_steps() -> list[Step]:
    return [EnsureDirectories(), WriteConfig(), CreateVenv(), InstallPythonDeps()]
