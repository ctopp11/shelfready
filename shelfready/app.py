"""ShelfReady web app: a local HTTP server, standard library only.

Run:  python -m shelfready        then open http://127.0.0.1:8787
"""

from __future__ import annotations

import json
import os
import sys
import time
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import __version__, kdp_rules, prompt
from .ollama_client import OllamaError, chat_stream, has_model, installed_models, parse_json

MODEL = os.environ.get("SHELFREADY_MODEL", "gemma3:4b")
HOST = os.environ.get("SHELFREADY_HOST", "127.0.0.1")
PORT = int(os.environ.get("SHELFREADY_PORT", "8787"))
STATIC = Path(__file__).parent / "static"
MAX_BODY = 32 * 1024


def validate_brief(data: dict) -> dict:
    """Return a clean brief or raise ValueError with a user-facing message."""
    theme = str(data.get("theme", "")).strip()
    if len(theme) < 3:
        raise ValueError("Tell me what the book is about. A theme of at least 3 characters is required.")
    if len(theme) > 200:
        raise ValueError("Keep the theme under 200 characters; put extra detail in Notes.")
    notes = str(data.get("notes", "")).strip()
    if len(notes) > 800:
        raise ValueError("Notes are limited to 800 characters.")
    pages = data.get("pages") or None
    if pages is not None:
        try:
            pages = int(pages)
        except (TypeError, ValueError):
            raise ValueError("Number of designs must be a whole number.") from None
        if not 1 <= pages <= 500:
            raise ValueError("Number of designs should be between 1 and 500.")
    audience = data.get("audience", "adults")
    if audience not in prompt.AUDIENCES:
        raise ValueError("Pick an audience from the list.")
    style = data.get("style", "")
    if style and style not in prompt.STYLES:
        raise ValueError("Pick a style from the list.")
    marketplace = data.get("marketplace", "amazon.com")
    if marketplace not in {"amazon.com", "amazon.ca", "amazon.co.uk"}:
        raise ValueError("Unsupported marketplace.")
    return {
        "theme": theme, "notes": notes, "pages": pages, "audience": audience, "style": style,
        "trim": str(data.get("trim", ""))[:40], "marketplace": marketplace,
        "single_sided": bool(data.get("single_sided")), "large_print": bool(data.get("large_print")),
    }


class Handler(SimpleHTTPRequestHandler):
    server_version = f"ShelfReady/{__version__}"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC), **kwargs)

    def log_message(self, fmt, *args):  # quieter, single-line logs
        sys.stderr.write(f"[shelfready] {self.address_string()} {fmt % args}\n")

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    # ---------- helpers ----------
    def _json(self, status: int, payload: dict):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY:
            raise ValueError("Request body is missing or too large.")
        try:
            data = json.loads(self.rfile.read(length))
        except json.JSONDecodeError:
            raise ValueError("Request body must be JSON.") from None
        if not isinstance(data, dict):
            raise ValueError("Request body must be a JSON object.")
        return data

    # ---------- routes ----------
    def do_GET(self):
        if self.path == "/api/health":
            try:
                models = installed_models()
                ready = has_model(MODEL, models)
                self._json(200, {"ok": ready, "model": MODEL, "ollama": True,
                                 "message": "Ready" if ready else f"Model {MODEL} not installed",
                                 "hint": "" if ready else f"Run: ollama pull {MODEL}"})
            except OllamaError as exc:
                self._json(200, {"ok": False, "model": MODEL, "ollama": False, "message": exc.message, "hint": exc.hint})
            return
        if self.path.startswith("/api/"):
            self._json(404, {"error": "Not found"})
            return
        super().do_GET()

    def do_POST(self):
        try:
            data = self._read_json()
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
            return
        if self.path == "/api/check":
            raw = data.get("listing")
            if not isinstance(raw, dict):
                self._json(400, {"error": "Send a listing object to check."})
                return
            kws = raw.get("keywords", [])
            listing = {
                "title": str(raw.get("title", "")), "subtitle": str(raw.get("subtitle", "")),
                "description": str(raw.get("description", "")),
                "keywords": [str(k) for k in kws][:20] if isinstance(kws, list) else [],
            }
            issues = kdp_rules.check_listing(listing, str(data.get("marketplace", "amazon.com")))
            self._json(200, {"listing": listing, "issues": [i.to_dict() for i in issues], "score": kdp_rules.score(issues)})
        elif self.path == "/api/generate":
            try:
                brief = validate_brief(data)
            except ValueError as exc:
                self._json(400, {"error": str(exc)})
                return
            self._generate(brief)
        else:
            self._json(404, {"error": "Not found"})

    def _generate(self, brief: dict):
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.end_headers()
        disconnected = False

        def emit(event: dict):
            nonlocal disconnected
            if disconnected:
                return
            try:
                self.wfile.write((json.dumps(event) + "\n").encode())
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                disconnected = True

        def run(stage: str, messages: list[dict]) -> dict:
            emit({"type": "stage", "stage": stage})
            text, chars, last = "", 0, 0.0
            for piece in chat_stream(MODEL, messages, prompt.SCHEMA, should_stop=lambda: disconnected):
                text += piece
                chars += len(piece)
                if time.monotonic() - last > 0.25:
                    emit({"type": "progress", "stage": stage, "chars": chars})
                    last = time.monotonic()
            if disconnected:
                raise ConnectionAbortedError
            listing, fixes = kdp_rules.apply_fixes(kdp_rules.sanitise_listing(parse_json(text)), brief["marketplace"])
            applied.extend(f for f in fixes if f not in applied)
            return listing

        started = time.monotonic()
        applied: list[str] = []
        try:
            pre = kdp_rules.check_input(brief["theme"], brief["notes"])
            listing = run("drafting", prompt.build_messages(brief))
            issues = kdp_rules.check_listing(listing, brief["marketplace"])
            repaired = False
            errors = [i.message for i in issues if i.level == "error"]
            if errors:
                listing = run("repairing", prompt.build_repair_messages(brief, listing, errors))
                issues = kdp_rules.check_listing(listing, brief["marketplace"])
                repaired = True
            all_issues = pre + issues
            emit({"type": "result", "listing": listing, "issues": [i.to_dict() for i in all_issues],
                  "score": kdp_rules.score(issues), "repaired": repaired, "fixes": applied, "model": MODEL,
                  "seconds": round(time.monotonic() - started, 1)})
        except ConnectionAbortedError:
            return
        except OllamaError as exc:
            emit({"type": "error", "message": exc.message, "hint": exc.hint})
        except Exception as exc:  # last-resort guard so the UI never hangs
            self.log_message("generate failed: %r", exc)
            emit({"type": "error", "message": "Something went wrong while writing the listing.", "hint": str(exc)[:200]})


def main():
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"ShelfReady {__version__} on http://{HOST}:{PORT}  (model: {MODEL})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nBye.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
