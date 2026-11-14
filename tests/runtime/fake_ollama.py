#!/usr/bin/env python3
"""Testler için sahte Ollama sunucusu: `fake_ollama.py serve`.

- OLLAMA_NO_CLOUD=1 değilse ya da OLLAMA_HOST loopback değilse çıkış kodu 3 ile reddeder.
- Durumu OLLAMA_MODELS altında tutar (fake-models.json, blobs/), her isteği requests.log'a yazar.
- FAKE_OLLAMA_CRASH=1 → başlar başlamaz çıkar (başlatma hatası testi).
"""
import hashlib
import json
import os
import signal
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def _instance(schema):
    t = schema.get("type")
    if "enum" in schema:
        return schema["enum"][0]
    if t == "object":
        return {k: _instance(v) for k, v in schema.get("properties", {}).items()}
    if t == "array":
        return [_instance(schema.get("items", {"type": "string"}))]
    return {"integer": 1, "number": 1.5, "boolean": True, "string": "tamam"}.get(t, None)


def main():
    if len(sys.argv) < 2 or sys.argv[1] != "serve":
        print("ollama version is 0.0.0-fake")
        return 0
    if os.environ.get("OLLAMA_NO_CLOUD") != "1":
        print("OLLAMA_NO_CLOUD eksik", file=sys.stderr)
        return 3
    host, _, port = os.environ.get("OLLAMA_HOST", "").rpartition(":")
    if host not in ("127.0.0.1", "[::1]"):
        print(f"loopback olmayan host: {host}", file=sys.stderr)
        return 3
    if os.environ.get("FAKE_OLLAMA_CRASH") == "1":
        print("bilerek çöktü", file=sys.stderr)
        return 1
    store = Path(os.environ["OLLAMA_MODELS"])
    store.mkdir(parents=True, exist_ok=True)
    (store / "blobs").mkdir(exist_ok=True)
    reg_file = store / "fake-models.json"

    def registry():
        return json.loads(reg_file.read_text()) if reg_file.exists() else {}

    def save(reg):
        reg_file.write_text(json.dumps(reg))

    def write_manifest(name, model_digest):
        """Gerçek Ollama gibi manifests/ altına manifest yaz (kurulum denetimi bunu okur)."""
        repo, _, tag = name.partition(":")
        p = store / "manifests/registry.ollama.ai/library" / repo / (tag or "latest")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"schemaVersion": 2, "layers": [
            {"mediaType": "application/vnd.ollama.image.model", "digest": model_digest}]}))

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _log(self):
            with open(store / "requests.log", "a") as f:
                f.write(f"{self.command} {self.path}\n")

        def _send(self, code, obj=None, lines=None):
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            body = (b"".join(json.dumps(x).encode() + b"\n" for x in lines) if lines is not None
                    else json.dumps(obj).encode())
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            return self.rfile.read(n) if n else b""

        def do_HEAD(self):
            self._log()
            if self.path.startswith("/api/blobs/"):
                d = self.path.rsplit("/", 1)[1].replace(":", "-")
                self.send_response(200 if (store / "blobs" / d).exists() else 404)
                self.end_headers()
                return
            self.send_response(404)
            self.end_headers()

        def do_GET(self):
            self._log()
            if self.path == "/api/version":
                return self._send(200, {"version": "0.0.0-fake"})
            if self.path == "/api/tags":
                return self._send(200, {"models": [{"name": n, "digest": m["digest"]}
                                                   for n, m in registry().items()]})
            if self.path == "/api/ps":
                return self._send(200, {"models": [{"name": n, "size_vram": 0}
                                                   for n in registry()]})
            if self.path == "/":
                return self._send(200, "Ollama is running")
            self._send(404, {"error": "not found"})

        def do_POST(self):
            self._log()
            raw = self._body()
            if self.path.startswith("/api/blobs/"):
                d = self.path.rsplit("/", 1)[1]
                if "sha256:" + hashlib.sha256(raw).hexdigest() != d:
                    return self._send(400, {"error": "digest mismatch"})
                (store / "blobs" / d.replace(":", "-")).write_bytes(raw)
                return self._send(201, {})
            req = json.loads(raw or b"{}")
            name = req.get("model", "")
            if ":" not in name and name:
                name += ":latest"
            reg = registry()
            if self.path == "/api/show":
                if name not in reg:
                    return self._send(404, {"error": f"model '{req.get('model')}' not found"})
                return self._send(200, {"details": {"family": "fake"}, "digest": reg[name]["digest"]})
            if self.path == "/api/generate":
                if name not in reg:
                    return self._send(404, {"error": f"model '{req.get('model')}' not found"})
                prompt = req.get("prompt", "")
                if "THINK_LEAK" in prompt:
                    body = (json.dumps(_instance(req["format"])) if isinstance(req.get("format"), dict)
                            else "4")
                    text = "Thinking Process:\n1. say\u0131lar...\n</think>\n\n" + body
                elif "BOZUK_JSON" in prompt:
                    text = '{"eksik": '
                elif "SEMA_DISI" in prompt:
                    text = json.dumps({"beklenmeyen": 1})
                elif isinstance(req.get("format"), dict):
                    text = json.dumps(_instance(req["format"]), ensure_ascii=False)
                else:
                    text = "TAMAM"
                return self._send(200, {"response": text, "done": True, "eval_count": 3,
                                        "total_duration": 1000})
            if self.path == "/api/pull":
                data = b"GGUF" + name.encode()
                d = "sha256:" + hashlib.sha256(data).hexdigest()
                (store / "blobs" / d.replace(":", "-")).write_bytes(data)
                reg[name] = {"digest": d}
                save(reg)
                write_manifest(name, d)
                return self._send(200, lines=[
                    {"status": "pulling manifest"},
                    {"status": "downloading", "total": 100, "completed": 50},
                    {"status": "downloading", "total": 100, "completed": 100},
                    {"status": "success"}])
            if self.path == "/api/create":
                files = req.get("files") or {}
                for digest in files.values():
                    if not (store / "blobs" / digest.replace(":", "-")).exists():
                        return self._send(400, {"error": f"blob {digest} not found"})
                reg[name] = {"digest": "sha256:" + hashlib.sha256(json.dumps(req, sort_keys=True)
                                                                  .encode()).hexdigest(),
                             "request": req}
                save(reg)
                write_manifest(name, files.get("model.gguf", ""))
                return self._send(200, {"status": "success"})
            self._send(404, {"error": "not found"})

    srv = ThreadingHTTPServer((host.strip("[]"), int(port)), H)
    signal.signal(signal.SIGTERM, lambda *a: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
