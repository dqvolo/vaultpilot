"""Reads a portal form and maps each field to a vault key.

Deterministic keyword rules go first. Fields they cannot place are offered to a local LLM
(Ollama) if one is running. For this step the LLM only sees field LABELS and key names,
never your values.
"""
import json, re
from html.parser import HTMLParser

from . import llm

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


DESC = {"name": "the applicant's own full name", "dob": "the applicant's date of birth",
        "gender": "the applicant's gender", "aadhaar": "the applicant's 12-digit Aadhaar (UID) number",
        "pan": "the applicant's PAN card number", "mobile": "the applicant's mobile phone number",
        "email": "the applicant's email address", "pincode": "the 6-digit postal PIN code",
        "address": "the applicant's residential address"}
RELATIVE = r"father|mother|guardian|spouse|parent|husband|wife|nominee"


def _ask_llm(unmapped: list) -> dict:
    """Ask the local model about fields the rules couldn't place.

    It receives field LABELS and the list of KEY NAMES with descriptions. It never sees your values.
    Answers about other people (father, mother, ...) are rejected here, outside the model."""
    if not unmapped:
        return {}
    labels = [f["label"] for f in unmapped]
    prompt = ("You map labels on an Indian government form to the applicant's own saved details.\n"
              f"Available keys: {json.dumps(DESC)}\n"
              f"Form labels: {json.dumps(labels)}\n"
              "For each label give the single best key, or null if it is about someone else "
              "(father, mother, guardian...), or does not match any key. Do not guess.\n"
              'Reply with JSON only: {"<label>": "<key or null>"}')
    r = llm.generate_json(prompt)
    if not isinstance(r, dict):
        return {}
    out = {}
    for label, key in r.items():
        low = label.lower()
        if label in labels and key in DESC and not re.search(RELATIVE, low) \
                and not (key == "name" and re.search(NOT_NAME, low)):
            out[label] = key
    return out


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
    guesses = _ask_llm([f for f in res if f["status"] == "unmapped"])
    for f in res:
        if f["status"] == "unmapped" and f["label"] in guesses:
            f["key"], f["source"], f["status"] = guesses[f["label"]], "llm", "fill"
    for f in res:  # a key the vault doesn't have can't be filled
        if f["status"] == "fill" and f["key"] not in vault_keys:
            f["status"] = "missing"
    return res
