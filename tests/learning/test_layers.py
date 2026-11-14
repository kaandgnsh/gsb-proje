"""Katman kuralı (spec §5): çekirdek saf kalır; ağ yalnız lai.ai → lai_runtime.client üzerinden."""
import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "lai"


def imports(pkg: str) -> dict[str, set[str]]:
    out = {}
    for f in (SRC / pkg).rglob("*.py"):
        mods = set()
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                mods |= {a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                mods.add(node.module)
        out[f.name] = mods
    return out


NET = ("socket", "urllib", "http", "requests", "httpx", "ssl", "subprocess")


@pytest.mark.parametrize("pkg", ["core", "store", "ingest", "learning"])
def test_no_qt_outside_ui(pkg):
    for f, mods in imports(pkg).items():
        assert not any(m.startswith("PySide6") for m in mods), f


def test_core_imports_no_ai_no_network():
    for f, mods in imports("core").items():
        bad = [m for m in mods if m.startswith(("lai.ai", "lai_runtime.client", "lai.learning",
                                                "lai.store")) or m.split(".")[0] in NET]
        assert not bad, (f, bad)


@pytest.mark.parametrize("pkg", ["learning", "ingest"])
def test_learning_and_ingest_have_no_direct_network(pkg):
    for f, mods in imports(pkg).items():
        bad = [m for m in mods if m.split(".")[0] in NET or m.startswith("lai_runtime.client")]
        assert not bad, (f, bad)


def test_ai_uses_only_loopback_client():
    mods = set().union(*imports("ai").values())
    assert not [m for m in mods if m.split(".")[0] in NET]
    assert "lai_runtime.client" in mods
