"""Thin client for a LOCAL Ollama model. Nothing here talks to the internet.

If Ollama isn't running (or the model isn't pulled), every call returns None and the app
quietly falls back to rules only.
"""
import json, os, urllib.request

HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")


def model() -> str:
    return os.environ.get("VAULTPILOT_MODEL", "qwen2.5:7b")


def disabled() -> bool:
    return bool(os.environ.get("VAULTPILOT_NO_LLM"))


def status() -> dict:
    """{'available': bool, 'model': str, 'reason': str|None} for the UI badge."""
    if disabled():
        return {"available": False, "model": model(), "reason": "AI disabled"}
    try:
        tags = json.loads(urllib.request.urlopen(f"{HOST}/api/tags", timeout=2).read())
    except Exception:
        return {"available": False, "model": model(), "reason": "Ollama is not running"}
    want = model() if ":" in model() else model() + ":latest"
    if want not in [m.get("name") for m in tags.get("models", [])]:
        return {"available": False, "model": model(), "reason": f"model not pulled: ollama pull {model()}"}
    return {"available": True, "model": model(), "reason": None}


def generate(prompt: str, json_mode: bool = False):
    """Returns the model's text, or None on any failure. keep_alive keeps it loaded between calls."""
    if disabled():
        return None
    body = {"model": model(), "prompt": prompt, "stream": False, "keep_alive": "30m",
            "options": {"temperature": 0}}
    if json_mode:
        body["format"] = "json"
    try:
        req = urllib.request.Request(f"{HOST}/api/generate", json.dumps(body).encode(),
                                     {"Content-Type": "application/json"})
        timeout = int(os.environ.get("VAULTPILOT_LLM_TIMEOUT", "90"))
        return json.loads(urllib.request.urlopen(req, timeout=timeout).read())["response"]
    except Exception:
        return None


def generate_json(prompt: str):
    out = generate(prompt, json_mode=True)
    try:
        return json.loads(out) if out else None
    except ValueError:
        return None
