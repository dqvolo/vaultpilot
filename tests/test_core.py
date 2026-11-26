import os, tempfile
from datetime import date
os.environ["VAULTPILOT_HOME"] = tempfile.mkdtemp()
os.environ["VAULTPILOT_NO_KEYRING"] = "1"
os.environ["VAULTPILOT_NO_LLM"] = "1"

from app import mapper, ocr, rules, vault
from pathlib import Path

PORTAL = (Path(__file__).parent.parent / "app/portal/scholarship.html").read_text()
GOOD = "23456789012" + rules.verhoeff_check_digit("23456789012")


def test_aadhaar_checksum():
    assert rules.verhoeff_valid(GOOD)
    bad = GOOD[:-1] + str((int(GOOD[-1]) + 1) % 10)
    assert any("checksum" in i["msg"] for i in rules.validate_fields({"aadhaar": bad}))
    assert rules.validate_fields({"aadhaar": GOOD, "pan": "ABCPK1234F", "mobile": "9876543210"}) == []


def test_bad_fields():
    msgs = " ".join(i["msg"] for i in rules.validate_fields({"pan": "abc", "mobile": "12345", "dob": "01/01/2999"}))
    assert "PAN" in msgs and "Mobile" in msgs and "future" in msgs


def test_cross_check():
    docs = {"aadhaar": {"name": "Asha Kumari", "dob": "14/08/2004"}, "pan": {"name": "Kumari Asha", "dob": "14/08/2004"}}
    assert rules.cross_check(docs) == []          # same words, different order: fine
    docs["pan"]["name"] = "Asha K"
    assert rules.cross_check(docs)[0]["level"] == "warn"
    docs["pan"]["dob"] = "15/08/2004"
    assert any(i["level"] == "error" for i in rules.cross_check(docs))


def test_deadline_and_docs():
    assert rules.check_deadline({"deadline": "2026-11-26"}, today=date(2026, 11, 24))[0]["level"] == "warn"
    assert rules.check_deadline({"deadline": "2026-11-01"}, today=date(2026, 11, 24))[0]["level"] == "error"
    assert rules.check_docs({"required-docs": "aadhaar,pan"}, {"pan": {}})


def test_fake_portal_detection():
    assert rules.check_url("http://scholarships-gov.in.verify-now.com/x")[0]["level"] == "error"
    assert rules.check_url("https://scholarships.gov.in/")  == []
    assert rules.check_url("https://xn--incometax-9ya.com")[0]["level"] == "error"


def test_ocr_extract():
    text = f"Government of India\nAsha Kumari\nDOB: 14/08/2004\nFemale\n{GOOD[:4]} {GOOD[4:8]} {GOOD[8:]}"
    r = ocr.extract(text)
    assert r["doc_type"] == "aadhaar" and r["fields"]["name"] == "Asha Kumari" and r["fields"]["aadhaar"] == GOOD
    p = ocr.extract("INCOME TAX DEPARTMENT\nName\nASHA KUMARI\nDate of Birth\n14/08/2004\nABCPK1234F")
    assert p["doc_type"] == "pan" and p["fields"]["pan"] == "ABCPK1234F" and p["fields"]["name"] == "Asha Kumari"


def test_mapper_on_mock_portal():
    page = mapper.parse(PORTAL)
    keys = ["name", "dob", "gender", "aadhaar", "pan", "mobile", "email", "address", "pincode"]
    by = {f["label"]: f for f in mapper.map_fields(page["fields"], keys)}
    assert by["Applicant's Full Name"]["key"] == "name"
    assert by["Father's Name"]["status"] == "unmapped"        # not the user's own name
    assert by["UID Number"]["key"] == "aadhaar"
    assert by["Permanent Account Number"]["key"] == "pan"
    assert by["OTP sent to your mobile"]["status"] == "handoff"  # never automated
    assert page["meta"]["required-docs"] == "aadhaar,pan"


def test_vault_is_encrypted_and_wipes():
    vault.save_doc("aadhaar", {"aadhaar": GOOD, "name": "Asha Kumari"})
    raw = (vault._home() / "vault.bin").read_bytes()
    assert GOOD.encode() not in raw and b"Asha" not in raw
    assert vault.profile()["name"] == "Asha Kumari"
    vault.log("localhost:8000", ["name"])
    assert "Asha" not in str(vault.audit())                   # audit stores keys, not values
    vault.wipe()
    assert vault.docs() == {}


def test_analyze_runs_url_check_even_if_page_offline():
    from fastapi.testclient import TestClient
    from app.main import app
    r = TestClient(app).post("/api/analyze", json={"url": "http://scholarships-gov.in.verify-now.com/refund"}).json()
    assert r["fields"] == [] and any(i["level"] == "error" for i in r["issues"])
