"""Reads a portal form and maps each field to a vault key.

Deterministic keyword rules go first. Fields they cannot place are offered to a local LLM
(Ollama) if one is running. The LLM only ever sees field LABELS and vault KEY NAMES,
never your values.
"""
import json, os, re, urllib.request
from html.parser import HTMLParser

# Ordered: specific before generic. First match wins.
RULES = [
    ("aadhaar", r"aadhaar|aadhar|\buid\b"),
    ("pan", r"\bpan\b|permanent account"),
    ("mobile", r"mobile|phone|contact no"),
    ("email", r"e-?mail"),
    ("pincode", r"pin ?code|postal|zip"),
    ("dob", r"\bdob\b|birth"),
    ("gender", r"gender|\bsex\b"),
    ("address", r"address"),
    ("name", r"\bname\b|full_?name"),
]
NOT_NAME = r"father|mother|guardian|spouse|bank|college|school|institution|account"
HANDOFF = r"otp|captcha|password|verification code|security code"


class _Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.fields, self.labels, self.meta = [], {}, {}
        self.title, self._in_title, self._label_for, self._label_text = "", False, None, ""

    def handle_starttag(self, tag, a):
        a = dict(a)
        if tag == "meta" and a.get("name"):
            self.meta[a["name"]] = a.get("content", "")
        elif tag == "title":
            self._in_title = True
        elif tag == "label":
            self._label_for, self._label_text = a.get("for"), ""
        elif tag in ("input", "select", "textarea") and a.get("type", "text") not in (
                "hidden", "submit", "button", "checkbox", "radio"):
            self.fields.append({**a, "tag": tag})

    def handle_data(self, d):
        if self._label_for:
            self._label_text += d
        if self._in_title:
            self.title += d

    def handle_endtag(self, tag):
        if tag == "label" and self._label_for:
            self.labels[self._label_for] = self._label_text.strip()
            self._label_for = None
        elif tag == "title":
            self._in_title = False


def parse(html: str) -> dict:
    p = _Page()
    p.feed(html)
    fields = []
    for f in p.fields:
        fid, name = f.get("id"), f.get("name")
        if not (fid or name):
            continue
        fields.append({
            "selector": f'[id="{fid}"]' if fid else f'[name="{name}"]',
            "tag": f["tag"],
            "label": p.labels.get(fid, "") or f.get("placeholder", "") or name or fid,
            "hint": " ".join(filter(None, [name, fid, f.get("placeholder")])),
        })
    return {"title": p.title.strip(), "meta": p.meta, "fields": fields}


def _ask_llm(unmapped: list, keys: list) -> dict:
    """Optional: ask a local Ollama model. Silently returns {} if none is running."""
    if not unmapped or os.environ.get("VAULTPILOT_NO_LLM"):
        return {}
    prompt = ("Map each form field label to one of these keys, or null if none fits.\n"
              f"Keys: {json.dumps(keys)}\nFields: {json.dumps([f['label'] for f in unmapped])}\n"
              'Reply with JSON only: {"<label>": "<key or null>"}')
    body = json.dumps({"model": os.environ.get("VAULTPILOT_MODEL", "qwen2.5:7b"),
                       "prompt": prompt, "stream": False, "format": "json"}).encode()
    try:
        req = urllib.request.Request("http://localhost:11434/api/generate", body,
                                     {"Content-Type": "application/json"})
        out = json.loads(urllib.request.urlopen(req, timeout=20).read())["response"]
        return {k: v for k, v in json.loads(out).items() if v in keys}
    except Exception:
        return {}


def map_fields(fields: list, vault_keys: list) -> list:
    """Adds 'key' and 'status' ('fill' | 'handoff' | 'unmapped') to each field."""
    res = []
    for f in fields:
        text = f"{f['label']} {f['hint']}".lower()
        f = {**f, "key": None, "source": None}
        if re.search(HANDOFF, text):
            f["status"] = "handoff"          # OTP / CAPTCHA always go back to the user
        else:
            for key, rx in RULES:
                if re.search(rx, text) and not (key == "name" and re.search(NOT_NAME, text)):
                    f["key"], f["source"] = key, "rules"
                    break
            f["status"] = "fill" if f["key"] else "unmapped"
        res.append(f)
    guesses = _ask_llm([f for f in res if f["status"] == "unmapped"], vault_keys)
    for f in res:
        if f["status"] == "unmapped" and f["label"] in guesses:
            f["key"], f["source"], f["status"] = guesses[f["label"]], "llm", "fill"
    for f in res:  # a key the vault doesn't have can't be filled
        if f["status"] == "fill" and f["key"] not in vault_keys:
            f["status"] = "missing"
    return res
