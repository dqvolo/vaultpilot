"""On-device OCR (Tesseract) + field extraction from the recognised text."""
import io, re


def image_to_text(data: bytes) -> str:
    import os, shutil
    import pytesseract
    from PIL import Image
    win = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    pytesseract.pytesseract.tesseract_cmd = (
        os.environ.get("TESSERACT_CMD") or shutil.which("tesseract")
        or (win if os.path.exists(win) else "tesseract"))
    img = Image.open(io.BytesIO(data)).convert("L")      # grayscale
    if img.width < 1600:                                   # small text reads far better upscaled
        k = 1600 / img.width
        img = img.resize((1600, int(img.height * k)), Image.LANCZOS)
    try:
        return pytesseract.image_to_string(img, lang="eng+hin")
    except pytesseract.TesseractError:  # Hindi pack not installed
        return pytesseract.image_to_string(img, lang="eng")


def extract(text: str) -> dict:
    """Pull known fields out of OCR text. Heuristic on purpose: the user reviews and edits."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    flat = "\n".join(lines)
    f: dict = {}

    if m := re.search(r"\b([2-9]\d{3})\s?(\d{4})\s?(\d{4})\b", flat):
        f["aadhaar"] = "".join(m.groups())
    if m := re.search(r"\b[A-Z]{5}\d{4}[A-Z]\b", flat):
        f["pan"] = m.group(0)
    if m := re.search(r"\b(\d{2})[/-](\d{2})[/-](\d{4})\b", flat):
        f["dob"] = f"{m.group(1)}/{m.group(2)}/{m.group(3)}"
    if m := re.search(r"\b(male|female|transgender)\b", flat, re.I):
        f["gender"] = m.group(1).capitalize()
    if m := re.search(r"\b[1-9]\d{5}\b", flat):
        f["pincode"] = m.group(0)
    if m := re.search(r"\b[6-9]\d{9}\b", flat):
        f["mobile"] = m.group(0)

    # Name: the line after a bare "Name" label (PAN) or the line before the DOB line (Aadhaar).
    for i, l in enumerate(lines):
        if re.fullmatch(r"name", l, re.I) and i + 1 < len(lines):
            f["name"] = lines[i + 1].title(); break
    if "name" not in f:
        for i, l in enumerate(lines):
            if re.search(r"dob|date of birth|year of birth", l, re.I) and i > 0:
                if re.fullmatch(r"[A-Za-z .]{3,}", lines[i - 1]):
                    f["name"] = lines[i - 1].title(); break

    doc_type = "pan" if "pan" in f and "aadhaar" not in f else "aadhaar" if "aadhaar" in f else "other"
    return {"doc_type": doc_type, "fields": f}


def ai_fill(text: str, fields: dict):
    """If OCR text yielded no name or DOB, ask the LOCAL model. Returns (fields, keys_filled_by_ai).

    Anti-hallucination: an AI answer is kept only if its words/digits literally appear in the
    OCR text. Regex results are never overridden."""
    from . import llm
    missing = [k for k in ("name", "dob") if k not in fields]
    if not missing or not llm.status()["available"]:
        return fields, []
    r = llm.generate_json(
        "This text was read by OCR from an Indian identity document. Extract the holder's own "
        "full name and date of birth (DD/MM/YYYY). Use null if unsure; never guess.\n"
        'Reply with JSON only: {"name": "...", "dob": "..."}\nTEXT:\n' + text[:2000]) or {}
    low, out, used = text.lower(), dict(fields), []
    n = r.get("name")
    if "name" in missing and isinstance(n, str) and re.fullmatch(r"[A-Za-z][A-Za-z .']{2,59}", n.strip()) \
            and all(t in low for t in n.lower().split()):
        out["name"] = n.strip().title(); used.append("name")
    d = r.get("dob")
    if "dob" in missing and isinstance(d, str) and re.fullmatch(r"\d{2}/\d{2}/\d{4}", d.strip()) \
            and all(part in text for part in d.strip().split("/")):
        out["dob"] = d.strip(); used.append("dob")
    return out, used
