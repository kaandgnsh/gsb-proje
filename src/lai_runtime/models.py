"""Çevrimdışı model içe aktarma.

Kaynak: başka bir Ollama model dizini (manifests/ ya da manifests-v2/ düzeni) veya tek bir
.gguf dosyası. Kaynağa asla yazılmaz; ağırlıklar özet (sha256) doğrulanarak izole çalışma
ortamına Ollama API'si (blob yükleme + create) üzerinden aktarılır. Böylece hedef dizinin
iç düzeni Ollama sürümüne bırakılır.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .client import OllamaClient

MT_MODEL = "application/vnd.ollama.image.model"
MT_PROJECTOR = "application/vnd.ollama.image.projector"
MT_TEMPLATE = "application/vnd.ollama.image.template"
MT_SYSTEM = "application/vnd.ollama.image.system"
MT_PARAMS = "application/vnd.ollama.image.params"
MT_ADAPTER = "application/vnd.ollama.image.adapter"
_HOSTS = ("registry.ollama.ai", "ollama.com")
_LAYOUTS = ("manifests", "manifests-v2")


class ModelSourceError(RuntimeError):
    pass


@dataclass
class SourceModel:
    name: str
    gguf: Path
    gguf_digest: str
    template: str | None = None
    system: str | None = None
    parameters: dict = field(default_factory=dict)
    extra_files: dict[str, tuple[Path, str]] = field(default_factory=dict)  # ad -> (yol, özet)


def split_name(name: str) -> tuple[str | None, str, str, str]:
    """'model', 'model:tag', 'ns/model:tag', 'host/ns/model:tag' -> (host, ns, repo, tag)."""
    parts = name.split("/")
    last, _, tag = parts[-1].partition(":")
    parts[-1], tag = last, tag or "latest"
    if len(parts) == 1:
        return None, "library", parts[0], tag
    if len(parts) == 2:
        return None, parts[0], parts[1], tag
    return parts[0], parts[1], "/".join(parts[2:]), tag


def _blob_path(models_dir: Path, digest: str) -> Path:
    return models_dir / "blobs" / digest.replace(":", "-")


def _sha256(path: Path, on_progress: Callable[[dict], None] | None = None) -> str:
    h = hashlib.sha256()
    total, done = path.stat().st_size, 0
    with open(path, "rb") as f:
        while chunk := f.read(8 * 1024 * 1024):
            h.update(chunk)
            done += len(chunk)
            if on_progress:
                on_progress({"status": "doğrulanıyor", "total": total, "completed": done})
    return "sha256:" + h.hexdigest()


def find_source_model(models_dir: Path, name: str) -> SourceModel:
    models_dir = Path(models_dir)
    host, ns, repo, tag = split_name(name)
    hosts = (host,) if host else _HOSTS
    manifest_path = None
    for layout in _LAYOUTS:
        for h in hosts:
            p = models_dir / layout / h / ns / repo / tag
            if p.exists():
                manifest_path = p
                break
        if manifest_path:
            break
    if manifest_path is None:
        raise ModelSourceError(f"kaynak dizinde model bulunamadı: {name} ({models_dir})")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ModelSourceError(f"manifest okunamadı: {manifest_path}: {e}") from None

    src: dict = {"template": None, "system": None, "parameters": {}, "extra": {}}
    gguf = None
    for layer in manifest.get("layers", []):
        mt, digest = layer.get("mediaType"), layer.get("digest", "")
        path = _blob_path(models_dir, digest)
        if mt in (MT_MODEL, MT_PROJECTOR, MT_ADAPTER) and not path.is_file():
            raise ModelSourceError(f"blob eksik: {path}")
        if mt == MT_MODEL:
            gguf = (path, digest)
        elif mt == MT_PROJECTOR:
            src["extra"][f"projector-{len(src['extra'])}.gguf"] = (path, digest)
        elif mt == MT_ADAPTER:
            raise ModelSourceError("LoRA adaptörlü modeller içe aktarılamaz")
        elif mt == MT_TEMPLATE:
            src["template"] = path.read_text(encoding="utf-8")
        elif mt == MT_SYSTEM:
            src["system"] = path.read_text(encoding="utf-8")
        elif mt == MT_PARAMS:
            src["parameters"] = json.loads(path.read_text(encoding="utf-8"))
    if gguf is None:
        raise ModelSourceError(f"manifestte model katmanı yok: {manifest_path}")
    return SourceModel(name, gguf[0], gguf[1], src["template"], src["system"],
                       src["parameters"], src["extra"])


def source_from_gguf(path: Path, name: str) -> SourceModel:
    path = Path(path)
    with open(path, "rb") as f:
        if f.read(4) != b"GGUF":
            raise ModelSourceError(f"GGUF dosyası değil: {path}")
    return SourceModel(name, path, _sha256(path))


def import_model(client: OllamaClient, source: SourceModel, target_name: str,
                 on_progress: Callable[[dict], None] | None = None) -> str:
    """Kaynağı doğrular, izole sunucuya yükler, `target_name` adıyla oluşturur.

    Dönüş: ağırlık dosyasının özeti (sürüm sabitleme için).
    """
    files: dict[str, str] = {}
    for fname, (path, digest) in {"model.gguf": (source.gguf, source.gguf_digest),
                                  **source.extra_files}.items():
        actual = _sha256(path, on_progress)
        if actual != digest:
            raise ModelSourceError(f"özet uyuşmuyor ({path.name}): beklenen {digest}, bulunan {actual}")
        if on_progress:
            on_progress({"status": f"aktarılıyor: {fname}"})
        client.upload_blob(path, digest)
        files[fname] = digest
    client.create(target_name, files, template=source.template, system=source.system,
                  parameters=source.parameters or None)
    return source.gguf_digest
