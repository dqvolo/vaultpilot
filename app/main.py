"""FastAPI backend. Bind to 127.0.0.1 only: nothing here is meant to be reachable from outside."""
import re, threading, urllib.request
from html.parser import HTMLParser
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from . import agent, llm, mapper, ocr, rules, vault

BASE = Path(__file__).parent
app = FastAPI(title="VaultPilot")


@app.on_event("startup")
def _warm_up_model():
    """Load the model in the background so the first real request isn't slow."""
    threading.Thread(target=lambda: llm.status()["available"] and llm.generate("Reply OK."),
                     daemon=True).start()


@app.get("/api/ai/status")
def ai_status():
    return llm.status()


def _fetch(url: str) -> str:
    if urlparse(url).scheme not in ("http", "https"):
        raise HTTPException(400, "Only http(s) addresses are supported.")
    return urllib.request.urlopen(url, timeout=10).read().decode("utf-8", "replace")


@app.get("/")
def index():
    return FileResponse(BASE / "static" / "index.html")


@app.get("/portal/{name}")
def portal(name: str):
    """Mock government portal. The deadline is always 5 days from today so the warning shows."""
    f = BASE / "portal" / f"{name}.html"
    if not f.is_file():
        raise HTTPException(404)
    return HTMLResponse(f.read_text().replace("{{DEADLINE}}", str(date.today() + timedelta(days=5))))


@app.post("/api/scan")
async def scan(file: UploadFile):
    try:
        text = ocr.image_to_text(await file.read())
    except Exception as e:
        raise HTTPException(500, f"OCR failed (is Tesseract installed?): {e}")
    res = ocr.extract(text)
    res["fields"], ai_filled = ocr.ai_fill(text, res["fields"])
    return {"text": text, **res, "ai_filled": ai_filled}


class DocIn(BaseModel):
    doc_type: str
    fields: dict


@app.post("/api/vault")
def save(doc: DocIn):
    vault.save_doc(doc.doc_type, doc.fields)
    return {"issues": rules.validate_fields(doc.fields) + rules.cross_check(vault.docs())}


@app.get("/api/vault")
def read_vault():
    return {"docs": vault.docs(), "audit": vault.audit()}


@app.delete("/api/vault")
def wipe():
    vault.wipe()
    return {"ok": True}


class UrlIn(BaseModel):
    url: str


@app.post("/api/analyze")
def analyze(body: UrlIn):
    if urlparse(body.url).scheme not in ("http", "https"):
        raise HTTPException(400, "Only http(s) addresses are supported.")
    try:
        html = _fetch(body.url)
    except Exception as e:  # still run the URL safety check: a fake portal may simply be offline
        return {"title": "", "fields": [], "issues": rules.check_url(body.url) + [
            {"level": "warn", "msg": f"Could not load the page ({e.__class__.__name__}).", "field": None}]}
    page = mapper.parse(html)
    docs, prof = vault.docs(), vault.profile()
    fields = mapper.map_fields(page["fields"], list(prof))
    for f in fields:
        f["value"] = prof.get(f["key"], "") if f["status"] == "fill" else ""
    issues = (rules.check_url(body.url) + rules.check_deadline(page["meta"])
              + rules.check_docs(page["meta"], docs) + rules.cross_check(docs)
              + rules.validate_fields({f["key"]: f["value"] for f in fields if f["status"] == "fill"}))
    ai = llm.status()
    hard = [f for f in fields if f["status"] == "unmapped"]
    if hard and not ai["available"]:
        issues.append({"level": "info", "field": None, "msg":
                       f"{len(hard)} field(s) could not be matched by rules. AI is off ({ai['reason']}), "
                       "so they are left for you."})
    return {"title": page["title"], "fields": fields, "issues": issues, "ai": ai}


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts, self.skip = [], 0
    def handle_starttag(self, t, a):
        self.skip += t in ("script", "style")
    def handle_endtag(self, t):
        self.skip -= t in ("script", "style")
    def handle_data(self, d):
        if not self.skip and d.strip():
            self.parts.append(d.strip())


@app.post("/api/explain")
def explain(body: UrlIn):
    """Plain-language summary of a form page. Only the page text goes to the local model, never your vault."""
    if not llm.status()["available"]:
        raise HTTPException(503, f"AI is off ({llm.status()['reason']}).")
    try:
        html = _fetch(body.url)
    except Exception as e:
        raise HTTPException(400, f"Could not load page: {e}")
    t = _Text(); t.feed(html)
    text = " ".join(t.parts)[:4000]
    out = llm.generate(
        "You help Indian citizens understand a government form. Using ONLY the page text below, write "
        "4 to 6 short bullet points in simple English: what the form is for, who can apply, documents "
        "needed, deadline and fees. If something is not in the text, write 'not stated'. Do not invent rules.\n\n"
        "PAGE TEXT:\n" + text)
    if not out:
        raise HTTPException(504, "The model did not answer in time.")
    return {"summary": out.strip()}


class FillIn(BaseModel):
    url: str
    items: list[dict]          # [{selector, tag, key, value}] already approved by the user
    force: bool = False        # user accepted a red URL warning


@app.post("/api/fill")
def fill(body: FillIn):
    if any(i["level"] == "error" for i in rules.check_url(body.url)) and not body.force:
        raise HTTPException(409, "Portal failed the safety check. Confirm to continue anyway.")
    prof = vault.profile()
    items = [i for i in body.items if i.get("key") in prof and i.get("value")]
    vault.log(urlparse(body.url).netloc, [i["key"] for i in items])
    threading.Thread(target=agent.fill, args=(body.url, items), daemon=True).start()
    return {"filled": len(items), "note": "Browser opened. Review, enter OTP/CAPTCHA, then submit yourself."}
