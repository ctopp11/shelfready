"""A tiny, dependency-free client for a local Ollama server."""

from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request
from typing import Callable, Iterator

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
if not OLLAMA_HOST.startswith("http"):
    OLLAMA_HOST = "http://" + OLLAMA_HOST


class OllamaError(Exception):
    """An error with a message a non-developer can act on."""

    def __init__(self, message: str, hint: str = ""):
        super().__init__(message)
        self.message = message
        self.hint = hint


def _request(path: str, payload: dict | None = None, timeout: float = 5.0):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(OLLAMA_HOST + path, data=data,
                                 headers={"Content-Type": "application/json"},
                                 method="POST" if data else "GET")
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        if exc.code == 404 and "not found" in body.lower():
            raise OllamaError("The model isn't installed in Ollama yet.",
                              "Run: ollama pull gemma3:4b (or set SHELFREADY_MODEL to a model you have).") from exc
        raise OllamaError(f"Ollama returned HTTP {exc.code}.", body[:300]) from exc
    except (urllib.error.URLError, ConnectionError, socket.timeout) as exc:
        raise OllamaError("Can't reach Ollama on this computer.",
                          f"Start it with `ollama serve` (expected at {OLLAMA_HOST}).") from exc


def installed_models() -> list[str]:
    with _request("/api/tags") as resp:
        return [m["name"] for m in json.load(resp).get("models", [])]


def has_model(name: str, models: list[str]) -> bool:
    wanted = name if ":" in name else name + ":latest"
    return wanted in models or name in models


def chat_stream(model: str, messages: list[dict], schema: dict, *,
                timeout: float = 600.0, should_stop: Callable[[], bool] = lambda: False) -> Iterator[str]:
    """Yield content fragments as the model writes them."""
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "format": schema,
        "options": {"temperature": 0.6, "num_predict": 1400, "num_ctx": 4096},
        "keep_alive": "30m",
    }
    try:
        with _request("/api/chat", payload, timeout=timeout) as resp:
            for line in resp:
                if should_stop():
                    return
                if not line.strip():
                    continue
                chunk = json.loads(line)
                if chunk.get("error"):
                    raise OllamaError("The model stopped with an error.", chunk["error"])
                piece = chunk.get("message", {}).get("content", "")
                if piece:
                    yield piece
                if chunk.get("done"):
                    return
    except OllamaError as exc:
        if "isn't installed" in exc.message:
            exc.hint = f"Run: ollama pull {model} (or set SHELFREADY_MODEL to a model you already have)."
        raise
    except socket.timeout as exc:
        raise OllamaError("The model took too long to respond.",
                          "Try a smaller model (e.g. gemma3:1b) or close other heavy apps.") from exc


def parse_json(text: str) -> dict:
    """Parse the model's JSON, tolerating stray prose or code fences around it."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass
    raise OllamaError("The model's answer wasn't valid JSON.", "Generate again; small models occasionally slip.")
