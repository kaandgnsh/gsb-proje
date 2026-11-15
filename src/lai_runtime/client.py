"""Ollama HTTP istemcisi — yalnız loopback, proxy yok, yönlendirme yok.

Modele giden her istek (`/api/version` dışında) bağlantı açılmadan önce doğrulanmış ağ
yalıtımı ister (`guard`). Tek istisna `download_only=True`: kurulum aracının açık izinli
indirmesi; bu istemci yalnız `/api/pull` gönderebilir, modeli asla çalıştıramaz.
"""
from __future__ import annotations

import ipaddress
import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .jsonschema_lite import validate


class OllamaError(RuntimeError):
    pass


class RuntimeUnavailableError(OllamaError):
    pass


class ModelIsolationError(OllamaError):
    """Yalıtım doğrulanmadı ya da indirme istemcisiyle model kullanılmak istendi."""


class ModelNotFoundError(OllamaError):
    pass


class StructuredOutputError(OllamaError):
    def __init__(self, message: str, raw: str):
        super().__init__(message)
        self.raw = raw


@dataclass
class GenerateResult:
    text: str
    data: object | None
    eval_count: int
    total_duration_ns: int
    thinking: str = ""


def split_thinking(text: str) -> tuple[str, str]:
    """Şablonu düşünmeyi ayırmayan modellerde yanıta karışan '...</think>' bölümünü ayırır."""
    if "</think>" not in text:
        return "", text
    thought, _, answer = text.rpartition("</think>")
    return thought.replace("<think>", "").strip(), answer.strip()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):  # yönlendirme ile dışarı çıkılamaz
        return None


def _check_loopback(base_url: str) -> str:
    u = urllib.parse.urlsplit(base_url)
    if u.scheme != "http" or not u.hostname or u.port is None:
        raise ValueError(f"yalnız http://<loopback>:<port> kabul edilir: {base_url}")
    try:
        ip = ipaddress.ip_address(u.hostname)
    except ValueError:
        raise ValueError(f"ana bilgisayar adı yerine loopback IP gerekir: {u.hostname}") from None
    if not ip.is_loopback:
        raise ValueError(f"loopback olmayan adres reddedildi: {u.hostname}")
    return f"http://{u.netloc}"


_ALWAYS = {("GET", "/api/version")}
_DOWNLOAD = {("POST", "/api/pull")}


class OllamaClient:
    def __init__(self, base_url: str, timeout: float = 600, *, download_only: bool = False):
        self.base_url = _check_loopback(base_url)
        self.timeout = timeout
        self.download_only = download_only
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

    # -- alt düzey --------------------------------------------------------------------------
    def _guard(self, method: str, path: str) -> None:
        if (method, path) in _ALWAYS:
            return
        if self.download_only:
            if (method, path) not in _DOWNLOAD:
                raise ModelIsolationError(
                    f"indirme istemcisi yalnız model indirebilir; {path} reddedildi")
            return
        from .guard import require_model_isolation

        require_model_isolation()

    def _open(self, method: str, path: str, data=None, headers=None, timeout=None):
        self._guard(method, path.split("?", 1)[0])
        req = urllib.request.Request(self.base_url + path, data=data, method=method,
                                     headers=headers or {})
        try:
            return self._opener.open(req, timeout=timeout or self.timeout)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            try:
                msg = json.loads(body).get("error", body)
            except ValueError:
                msg = body
            if e.code == 404 and "not found" in msg:
                raise ModelNotFoundError(f"model bulunamadı: {msg}") from None
            raise OllamaError(f"HTTP {e.code} {path}: {msg}") from None
        except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
            raise RuntimeUnavailableError(
                f"yerel AI çalışma ortamına ulaşılamadı ({self.base_url}): {e}") from None

    def _request(self, method: str, path: str, body: dict | None = None, *, timeout=None):
        data = json.dumps(body).encode() if body is not None else None
        with self._open(method, path, data, {"Content-Type": "application/json"}, timeout) as r:
            raw = r.read().decode("utf-8")
        return json.loads(raw) if raw.strip() else {}

    def _stream(self, path: str, body: dict, on_event: Callable[[dict], None] | None):
        data = json.dumps(body).encode()
        last = {}
        with self._open("POST", path, data, {"Content-Type": "application/json"}) as r:
            for line in r:
                if not line.strip():
                    continue
                ev = json.loads(line)
                if "error" in ev:
                    if "not found" in ev["error"] or "does not exist" in ev["error"]:
                        raise ModelNotFoundError(ev["error"])
                    raise OllamaError(ev["error"])
                last = ev
                if on_event:
                    on_event(ev)
        if last.get("status") != "success":
            raise OllamaError(f"{path} tamamlanmadı: {last}")
        return last

    # -- API --------------------------------------------------------------------------------
    def version(self) -> str:
        return self._request("GET", "/api/version", timeout=5)["version"]

    def list_models(self) -> list[dict]:
        return self._request("GET", "/api/tags", timeout=10).get("models", [])

    def ps(self) -> list[dict]:
        return self._request("GET", "/api/ps", timeout=10).get("models", [])

    def show(self, model: str) -> dict:
        return self._request("POST", "/api/show", {"model": model}, timeout=30)

    def generate(self, model: str, prompt: str, *, system: str | None = None,
                 schema: dict | None = None, options: dict | None = None,
                 think: bool = False, keep_alive: str | None = None) -> GenerateResult:
        body: dict = {"model": model, "prompt": prompt, "stream": False, "think": think}
        if system:
            body["system"] = system
        if schema is not None:
            body["format"] = schema
        if options:
            body["options"] = options
        if keep_alive is not None:
            body["keep_alive"] = keep_alive
        r = self._request("POST", "/api/generate", body)
        leaked, text = split_thinking(r.get("response", ""))
        thinking = (r.get("thinking") or "") + leaked
        data = None
        if schema is not None:
            try:
                data = json.loads(text)
            except ValueError:
                raise StructuredOutputError("model geçerli JSON üretmedi", text) from None
            errs = validate(data, schema)
            if errs:
                raise StructuredOutputError("JSON şemaya uymuyor: " + "; ".join(errs), text)
        return GenerateResult(text, data, int(r.get("eval_count", 0)),
                              int(r.get("total_duration", 0)), thinking)

    def pull(self, model: str, on_progress: Callable[[dict], None] | None = None) -> dict:
        return self._stream("/api/pull", {"model": model, "stream": True}, on_progress)

    def has_blob(self, digest: str) -> bool:
        try:
            with self._open("HEAD", f"/api/blobs/{digest}", timeout=30):
                return True
        except ModelIsolationError:
            raise
        except OllamaError:
            return False

    def upload_blob(self, path: Path, digest: str) -> None:
        if self.has_blob(digest):
            return
        size = path.stat().st_size
        with open(path, "rb") as f:
            with self._open("POST", f"/api/blobs/{digest}", data=f,
                            headers={"Content-Length": str(size),
                                     "Content-Type": "application/octet-stream"},
                            timeout=max(self.timeout, 3600)):
                pass

    def create(self, model: str, files: dict[str, str], *, template: str | None = None,
               system: str | None = None, parameters: dict | None = None) -> dict:
        body: dict = {"model": model, "files": files, "stream": False}
        if template:
            body["template"] = template
        if system:
            body["system"] = system
        if parameters:
            body["parameters"] = parameters
        r = self._request("POST", "/api/create", body, timeout=max(self.timeout, 3600))
        if r.get("status") != "success":
            raise OllamaError(f"model oluşturulamadı: {r}")
        return r
