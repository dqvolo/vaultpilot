# VaultPilot

> Fill any government form once, safely. Your data stays on your device.

**wHACKiest 2026 · Team Bit wizards · Team ID b435d1d1 · Track: Beyond the Browser**

A local-first agent that reads your ID documents, keeps them in an encrypted vault on your own machine,
and fills government-portal forms for you. It warns you about problems first and never clicks Submit.
**No document or value is sent to any cloud service.**

This repo is the 2-hour prototype of the idea in our deck. It covers the full loop on one demo form.

## What works

| Deck step | In this prototype |
|---|---|
| **Scan** | Tesseract OCR (English + Hindi pack if installed) reads an Aadhaar/PAN photo on-device. You review and correct every field. |
| **Store** | Fernet-encrypted vault file. Key lives in the OS keychain (0600 key file as fallback). One-click wipe. |
| **Understand** | Parses the portal form, maps fields to vault keys with keyword rules, and optionally asks a local Ollama model about fields the rules can't place. **The model sees only field labels and key names, never your values.** |
| **Fill & approve** | Shows every value, lets you untick or edit, then Playwright fills the form in a visible browser. **It stops there.** You enter OTP/CAPTCHA and press Submit yourself. |
| **Warnings** | Aadhaar Verhoeff checksum, PAN/mobile/PIN/email/DOB format, name or DOB mismatch across documents, deadline within 7 days, missing required documents, look-alike or non-HTTPS portal. |
| **Audit** | Every fill logs which portal got which field *names*, never the values. |

The rule engine (`app/rules.py`) runs outside the model, so the AI cannot override a safety rule.
OTP, CAPTCHA and password fields are always handed back to the user.

## Run it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
# Tesseract: sudo apt install tesseract-ocr tesseract-ocr-hin   (macOS: brew install tesseract tesseract-lang)

python samples/make_samples.py                      # makes FAKE sample ID images
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open http://localhost:8000, then:

1. Upload `samples/sample_aadhaar.png`, check the fields, **Save**. Repeat with `sample_pan.png`.
2. Click **Read form** (the mock scholarship portal is pre-filled). See the deadline warning and the field plan.
3. **Approve and fill**. A browser opens with the form filled and highlighted. The OTP box is left for you.
4. Try the fake URL from the hint to see the phishing warning.

Optional local AI: install [Ollama](https://ollama.com), run `ollama pull qwen2.5:7b`. Set `VAULTPILOT_MODEL` to use another model.

```bash
pytest -q        # 9 tests: rules, OCR extraction, form mapping, vault encryption
```

## Layout

```
app/
  main.py      FastAPI endpoints (localhost only)
  ocr.py       Tesseract + field extraction
  vault.py     Encrypted vault + audit log
  mapper.py    Form parser + field-to-vault mapping (+ optional Ollama)
  rules.py     Validators, mismatch/deadline/phishing checks
  agent.py     Playwright filler (never submits)
  static/      Review panel UI
  portal/      Mock government portal used for the demo
samples/       Fake ID generator
tests/
```

## Known limits (prototype)

- **Demo portal only.** The form reader uses plain HTTP fetch, so it works on the mock portal and public forms, not pages behind a login.
  Next step: read the DOM through Playwright after the user logs in.
- Fernet instead of SQLCipher, to avoid native build steps. Same idea: encrypted at rest, key in the keychain.
- OCR is heuristic and tuned on clean images. Real phone photos need pre-processing. The review step is there because of this.
- Not a Chrome extension yet; the panel is a local web page and the agent drives its own browser window.
- Sample IDs are synthetic. Do not commit real documents (`.vaultpilot/` and `samples/*.png` are git-ignored).
