"""Rule engine. Runs OUTSIDE the AI model, so the model cannot override a safety rule.

Every check returns a list of issues: {"level": "error"|"warn"|"info", "msg": str, "field": str|None}.
"""
import re
from datetime import date, datetime
from urllib.parse import urlparse

# --- Verhoeff checksum (the last digit of a real Aadhaar number) ---------------------------
_d = [[0,1,2,3,4,5,6,7,8,9],[1,2,3,4,0,6,7,8,9,5],[2,3,4,0,1,7,8,9,5,6],[3,4,0,1,2,8,9,5,6,7],
      [4,0,1,2,3,9,5,6,7,8],[5,9,8,7,6,0,4,3,2,1],[6,5,9,8,7,1,0,4,3,2],[7,6,5,9,8,2,1,0,4,3],
      [8,7,6,5,9,3,2,1,0,4],[9,8,7,6,5,4,3,2,1,0]]
_p = [[0,1,2,3,4,5,6,7,8,9],[1,5,7,6,2,8,3,0,9,4],[5,8,0,3,7,9,6,1,4,2],[8,9,1,6,0,4,3,5,2,7],
      [9,4,5,3,1,2,6,8,7,0],[4,2,8,6,5,7,3,9,0,1],[2,7,9,3,8,0,6,4,1,5],[7,0,4,6,9,1,3,2,5,8]]
_inv = [0,4,3,2,1,5,6,7,8,9]


def verhoeff_valid(num: str) -> bool:
    c = 0
    for i, ch in enumerate(reversed(num)):
        c = _d[c][_p[i % 8][int(ch)]]
    return c == 0


def verhoeff_check_digit(num: str) -> str:
    c = 0
    for i, ch in enumerate(reversed(num)):
        c = _d[c][_p[(i + 1) % 8][int(ch)]]
    return str(_inv[c])


def _i(level, msg, field=None):
    return {"level": level, "msg": msg, "field": field}


# --- Field validation -----------------------------------------------------------------------
def validate_fields(f: dict) -> list:
    out = []
    if a := f.get("aadhaar"):
        a = re.sub(r"\s", "", a)
        if not re.fullmatch(r"[2-9]\d{11}", a):
            out.append(_i("error", "Aadhaar must be 12 digits and cannot start with 0 or 1.", "aadhaar"))
        elif not verhoeff_valid(a):
            out.append(_i("error", "Aadhaar checksum fails: one digit is probably wrong (OCR error or typo).", "aadhaar"))
    if p := f.get("pan"):
        if not re.fullmatch(r"[A-Z]{5}\d{4}[A-Z]", p):
            out.append(_i("error", "PAN format is 5 letters, 4 digits, 1 letter.", "pan"))
        elif p[3] not in "ABCFGHLJPT":
            out.append(_i("warn", "4th character of PAN is not a valid holder type.", "pan"))
    if m := f.get("mobile"):
        if not re.fullmatch(r"[6-9]\d{9}", m):
            out.append(_i("error", "Mobile number must be 10 digits starting with 6-9.", "mobile"))
    if e := f.get("email"):
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", e):
            out.append(_i("error", "Email address looks incomplete.", "email"))
    if z := f.get("pincode"):
        if not re.fullmatch(r"[1-9]\d{5}", z):
            out.append(_i("error", "PIN code must be 6 digits.", "pincode"))
    if d := f.get("dob"):
        try:
            if datetime.strptime(d, "%d/%m/%Y").date() > date.today():
                out.append(_i("error", "Date of birth is in the future.", "dob"))
        except ValueError:
            out.append(_i("error", "Date of birth should be DD/MM/YYYY.", "dob"))
    return out


# --- Cross-document mismatch ----------------------------------------------------------------
def _norm_name(n: str) -> str:
    return " ".join(sorted(re.sub(r"[^a-z ]", "", n.lower()).split()))


def cross_check(docs: dict) -> list:
    out = []
    names = {t: d["name"] for t, d in docs.items() if d.get("name")}
    if len({_norm_name(n) for n in names.values()}) > 1:
        shown = " vs ".join(f"{t.upper()}: '{n}'" for t, n in names.items())
        out.append(_i("warn", f"Name differs between documents ({shown}). Portals often reject mismatches.", "name"))
    dobs = {t: d["dob"] for t, d in docs.items() if d.get("dob")}
    if len(set(dobs.values())) > 1:
        shown = " vs ".join(f"{t.upper()}: {v}" for t, v in dobs.items())
        out.append(_i("error", f"Date of birth differs between documents ({shown}).", "dob"))
    return out


# --- Deadlines and required documents -------------------------------------------------------
def check_deadline(meta: dict, today=None) -> list:
    raw = meta.get("deadline")
    if not raw:
        return []
    today = today or date.today()
    days = (datetime.strptime(raw, "%Y-%m-%d").date() - today).days
    if days < 0:
        return [_i("error", f"Deadline {raw} has passed. Expect a late fee or rejection.")]
    if days <= 7:
        return [_i("warn", f"Deadline is {raw}, only {days} day(s) away.")]
    return [_i("info", f"Deadline: {raw} ({days} days left).")]


def check_docs(meta: dict, docs: dict) -> list:
    need = [d.strip() for d in meta.get("required-docs", "").split(",") if d.strip()]
    missing = [d for d in need if d not in docs]
    return [_i("warn", f"This form needs {', '.join(missing)} but it is not in your vault yet.")] if missing else []


# --- Look-alike / fake portal check ---------------------------------------------------------
OFFICIAL_SUFFIXES = ("gov.in", "nic.in")
BAIT_WORDS = ("gov", "tax", "aadhaar", "aadhar", "uidai", "refund", "scholarship", "digilocker", "epfo", "kyc")


def check_url(url: str) -> list:
    u = urlparse(url)
    host = (u.hostname or "").lower()
    if host in ("localhost", "127.0.0.1"):
        return [_i("info", "Local demo portal (not a real government site).")]
    out = []
    official = any(host == s or host.endswith("." + s) for s in OFFICIAL_SUFFIXES)
    if not official:
        if any(w in host for w in BAIT_WORDS):
            out.append(_i("error", f"'{host}' uses government-sounding words but is NOT a .gov.in / .nic.in site. Possible fake portal."))
        else:
            out.append(_i("warn", f"'{host}' is not a .gov.in / .nic.in domain."))
    if u.scheme != "https":
        out.append(_i("error", "Connection is not HTTPS."))
    if "xn--" in host:
        out.append(_i("error", "Domain uses look-alike (punycode) characters."))
    return out
