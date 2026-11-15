import sys
import tomllib

from lai_setup import trial

from .test_bundle import bundle


def test_entry_routes_trial_outside_sandbox(tmp_path, monkeypatch):
    from lai_setup import package_entry

    source = bundle(tmp_path / "self")
    seen = []
    monkeypatch.delenv("LAI_SANDBOX_ACTIVE", raising=False)
    monkeypatch.setattr(trial, "main", lambda root, args: seen.append((root, args)) or 0)
    monkeypatch.setattr(sys, "argv", ["AppRun", str(source), "--deneme", "--yalniz-tani"])
    assert package_entry.main() == 0
    assert seen == [(source, ["--yalniz-tani"])]


def test_clean_refuses_root_without_marker(tmp_path):
    home = tmp_path / "kullanici"
    home.mkdir()
    (home / "onemli.txt").write_text("veri")
    assert trial.main(tmp_path, ["--kok", str(home), "--temizle"]) == 2
    assert (home / "onemli.txt").read_text() == "veri"


def test_refuses_non_empty_foreign_root(tmp_path):
    home = tmp_path / "kullanici"
    home.mkdir()
    (home / "onemli.txt").write_text("veri")
    assert trial.main(tmp_path, ["--kok", str(home), "--modelsiz", "--yalniz-tani"]) == 2
    assert sorted(p.name for p in home.iterdir()) == ["onemli.txt"]


def test_clean_removes_marked_root(tmp_path):
    home = tmp_path / "deneme"
    home.mkdir()
    (home / trial.MARKER).write_text("")
    (home / "logs").mkdir()
    assert trial.main(tmp_path, ["--kok", str(home), "--temizle"]) == 0
    assert not home.exists()


def test_missing_source_model_is_reported(tmp_path, capsys):
    store = tmp_path / "store"
    (store / "manifests/registry.ollama.ai/library/var").mkdir(parents=True)
    (store / "manifests/registry.ollama.ai/library/var/latest").write_text("{}")
    code = trial.main(
        tmp_path,
        ["--kok", str(tmp_path / "d"), "--kaynak", str(store), "--model", "yok:1b"],
    )
    assert code == 2
    assert "var:latest" in capsys.readouterr().err


def test_config_selects_trial_model(tmp_path, monkeypatch):
    paths = trial._paths(tmp_path / "d")
    trial._write_config(paths, 'model:"tırnak"')
    data = tomllib.loads(paths.config_file.read_text(encoding="utf-8"))["ollama"]
    assert data == {"model": 'model:"tırnak"', "port": 11435}
    monkeypatch.setenv("LAI_HOME", str(tmp_path / "d"))
    from lai.ui.window import _configured_model

    assert _configured_model() == 'model:"tırnak"'
