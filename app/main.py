"""FastAPI backend. Bind to 127.0.0.1 only: nothing here is meant to be reachable from outside."""
import threading, urllib.request
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from . import agent, mapper, ocr, rules, vault

BASE = Path(__file__).parent
app = FastAPI(title="VaultPilot")


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
    return {"text": text, **ocr.extract(text)}


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
    try:
        html = urllib.request.urlopen(body.url, timeout=10).read().decode("utf-8", "replace")
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
    return {"title": page["title"], "fields": fields, "issues": issues}


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
