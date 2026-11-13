import hashlib
import json
import os
import stat
from pathlib import Path

import pytest

from lai_runtime.client import OllamaClient
from lai_runtime.models import (ModelSourceError, find_source_model, import_model,
                                source_from_gguf)


def _blob(store: Path, data: bytes) -> str:
    d = "sha256:" + hashlib.sha256(data).hexdigest()
    (store / "blobs").mkdir(parents=True, exist_ok=True)
    (store / "blobs" / d.replace(":", "-")).write_bytes(data)
    return d


def make_store(root: Path, layout: str, name="demo", tag="latest", gguf=b"GGUF-sahte-agirlik"):
    model = _blob(root, gguf)
    params = _blob(root, json.dumps({"temperature": 0.2, "stop": ["<|im_end|>"]}).encode())
    tmpl = _blob(root, b"{{ .Prompt }}")
    cfg = _blob(root, b"{}")
    manifest = json.dumps({"schemaVersion": 2, "config": {"digest": cfg}, "layers": [
        {"mediaType": "application/vnd.ollama.image.model", "digest": model, "size": len(gguf)},
        {"mediaType": "application/vnd.ollama.image.params", "digest": params},
        {"mediaType": "application/vnd.ollama.image.template", "digest": tmpl}]}).encode()
    if layout == "v1":
        p = root / "manifests/registry.ollama.ai/library" / name / tag
        p.parent.mkdir(parents=True)
        p.write_bytes(manifest)
    else:
        mdig = _blob(root, manifest)
        p = root / "manifests-v2/ollama.com/library" / name / tag
        p.parent.mkdir(parents=True)
        os.symlink(Path("../../../../blobs") / mdig.replace(":", "-"), p)
    return model


def _snapshot(root: Path):
    return {str(p.relative_to(root)): (p.is_symlink(), hashlib.sha256(p.read_bytes()).hexdigest()
                                       if p.is_file() else None, p.stat().st_mtime_ns if p.exists() else 0)
            for p in sorted(root.rglob("*"))}


def _readonly(root: Path):
    for p in [root, *root.rglob("*")]:
        if not p.is_symlink():
            p.chmod(p.stat().st_mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))


def _writable(root: Path):
    for p in [root, *root.rglob("*")]:
        if not p.is_symlink():
            p.chmod(p.stat().st_mode | stat.S_IWUSR)


@pytest.mark.parametrize("layout", ["v1", "v2"])
def test_find_source_model(tmp_path, layout):
    digest = make_store(tmp_path / "src", layout)
    s = find_source_model(tmp_path / "src", "demo")
    assert s.gguf_digest == digest
    assert s.parameters == {"temperature": 0.2, "stop": ["<|im_end|>"]}
    assert s.template == "{{ .Prompt }}"
    assert find_source_model(tmp_path / "src", "demo:latest").gguf_digest == digest


def test_missing_source_model(tmp_path):
    make_store(tmp_path / "src", "v2")
    with pytest.raises(ModelSourceError, match="yok:1b"):
        find_source_model(tmp_path / "src", "yok:1b")


@pytest.mark.parametrize("layout", ["v1", "v2"])
def test_import_is_read_only_on_source(tmp_path, fake_server, layout):
    base, store, _ = fake_server
    src = tmp_path / "src"
    make_store(src, layout)
    _readonly(src)
    before = _snapshot(src)
    try:
        c = OllamaClient(base)
        import_model(c, find_source_model(src, "demo"), "lai-demo:1")
        assert "lai-demo:1" in [m["name"] for m in c.list_models()]
        req = json.loads((store / "fake-models.json").read_text())["lai-demo:1"]["request"]
        assert req["template"] == "{{ .Prompt }}"
        assert req["parameters"]["temperature"] == 0.2
        assert _snapshot(src) == before
    finally:
        _writable(src)


def test_corrupt_blob_rejected_and_nothing_created(tmp_path, fake_server):
    base, _, _ = fake_server
    src = tmp_path / "src"
    digest = make_store(src, "v2")
    (src / "blobs" / digest.replace(":", "-")).write_bytes(b"bozulmus")
    c = OllamaClient(base)
    with pytest.raises(ModelSourceError, match="özet"):
        import_model(c, find_source_model(src, "demo"), "lai-demo:1")
    assert c.list_models() == []


def test_import_from_gguf_file(tmp_path, fake_server):
    base, _, _ = fake_server
    g = tmp_path / "model.gguf"
    g.write_bytes(b"GGUF-tek-dosya")
    c = OllamaClient(base)
    import_model(c, source_from_gguf(g, "tek"), "tek:latest")
    assert [m["name"] for m in c.list_models()] == ["tek:latest"]


def test_gguf_magic_checked(tmp_path):
    g = tmp_path / "x.gguf"
    g.write_bytes(b"degil")
    with pytest.raises(ModelSourceError, match="GGUF"):
        source_from_gguf(g, "x")


@pytest.mark.parametrize("name,expected", [
    ("qwen3.5:4b", (None, "library", "qwen3.5", "4b")),
    ("qwen3.5", (None, "library", "qwen3.5", "latest")),
    ("kaan/model:v1", (None, "kaan", "model", "v1")),
    ("ollama.com/library/qwen3.5-9b-sansursuz:latest", ("ollama.com", "library", "qwen3.5-9b-sansursuz", "latest")),
    ("localhost:5000/ns/m", ("localhost:5000", "ns", "m", "latest")),
])
def test_split_name(name, expected):
    from lai_runtime.models import split_name
    assert split_name(name) == expected
