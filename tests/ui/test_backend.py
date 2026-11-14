import threading

import pytest
from synthetic_desktop import document, ready

from lai.ui.backend import Backend, Cancelled
from lai.ui.launcher import generate


def execute(backend, operation, args):
    return backend.execute(operation, args, threading.Event(), lambda data: None)


def test_cancel_before_preview_does_not_write(tmp_path):
    b = Backend(tmp_path / "cancel.db")
    event = threading.Event()
    event.set()
    with pytest.raises(Cancelled):
        b.execute("preview_document", {}, event, lambda data: None)
    assert not b.db.exists()


def test_file_change_after_preview_is_not_saved(tmp_path):
    db = tmp_path / "s.db"
    inv, *_ = ready(db)
    b = Backend(db)
    p = document(tmp_path / "new.json")
    preview = execute(
        b,
        "preview_document",
        {"path": str(p), "role": "inventory", "inventory_id": inv},
    )["preview"]
    p.write_text("{}")
    with pytest.raises(ValueError, match="değişti"):
        execute(b, "save_document", {"preview": preview})


def test_readonly_model_status_loopback_and_missing(monkeypatch):
    from lai.ai import status
    from lai_runtime.client import RuntimeUnavailableError

    class Fake:
        def __init__(self, *args, **kw):
            pass

        def list_models(self):
            return [{"name": "synthetic:1"}]

    monkeypatch.setattr(status, "OllamaClient", Fake)
    assert status.model_status(model="synthetic:1")["available"]
    assert not status.model_status(model="missing")["available"]
    with pytest.raises(ValueError):
        status.model_status(port=11434)

    class Down(Fake):
        def list_models(self):
            raise RuntimeUnavailableError("sentetik")

    monkeypatch.setattr(status, "OllamaClient", Down)
    assert "ulaşılamadı" in status.model_status()["message"]


@pytest.mark.parametrize("platform", ["linux", "win32"])
def test_launcher_generation_only_in_selected_directory(tmp_path, platform):
    result = generate(
        tmp_path / "Türkçe boşluk",
        tmp_path / "s.db",
        platform=platform,
        python="python",
    )
    assert (
        result.exists() and "Yerel Envanter" in result.read_text(encoding="utf-8")
        if platform == "linux"
        else result.exists()
    )
    assert (result.parent / "yerel-envanter.pyw").exists()


def test_bad_preferences_do_not_break_startup(tmp_path, windows):
    p = tmp_path / "bad.db"
    p.with_suffix(".ui.json").write_text(
        '{"page":{},"port":"bad","model":[],"inventory_id":[]}', encoding="utf-8"
    )
    w = windows(p)
    assert w.nav.currentRow() == 0 and w.port.value() == 11435
