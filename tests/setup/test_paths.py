from pathlib import Path

from lai_setup.paths import resolve_paths

HOME = Path("/home/u")


def test_linux_defaults():
    p = resolve_paths("linux", {}, HOME)
    base = HOME / ".local/share/local-assessment-ai"
    assert p.root == base
    assert p.app == base / "app"
    assert p.venv == base / "venv"
    assert p.data == base / "data"
    assert p.runtime == base / "runtime"
    assert p.models == base / "runtime/models"
    assert p.reports == base / "reports"
    assert p.logs == base / "logs"
    assert p.state_file == base / "install-state.json"
    assert p.config_dir == HOME / ".config/local-assessment-ai"
    assert p.config_file == HOME / ".config/local-assessment-ai/config.toml"


def test_linux_respects_xdg():
    p = resolve_paths("linux", {"XDG_DATA_HOME": "/x/data", "XDG_CONFIG_HOME": "/x/cfg"}, HOME)
    assert p.root == Path("/x/data/local-assessment-ai")
    assert p.config_file == Path("/x/cfg/local-assessment-ai/config.toml")


def test_windows_defaults():
    env = {"LOCALAPPDATA": r"C:\Users\u\AppData\Local", "APPDATA": r"C:\Users\u\AppData\Roaming"}
    p = resolve_paths("windows", env, HOME)
    assert p.root == Path(r"C:\Users\u\AppData\Local") / "LocalAssessmentAI"
    assert p.models == p.root / "runtime" / "models"
    assert p.config_file == Path(r"C:\Users\u\AppData\Roaming") / "LocalAssessmentAI" / "config.toml"


def test_lai_home_overrides_everything_with_turkish_and_spaces():
    p = resolve_paths("linux", {"LAI_HOME": "/tmp/a b/İş", "XDG_CONFIG_HOME": "/x"}, HOME)
    root = Path("/tmp/a b/İş")
    assert p.root == root
    assert p.config_file == root / "config" / "config.toml"
    for f in ("app", "venv", "data", "runtime", "models", "reports", "logs", "state_file", "config_dir"):
        assert getattr(p, f).is_relative_to(root)
