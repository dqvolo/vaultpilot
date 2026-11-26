"""Makes two FAKE ID images (synthetic data, valid checksum) so you can demo the OCR step."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.rules import verhoeff_check_digit

OUT = Path(__file__).parent
base = "23456789012"
AADHAAR = base + verhoeff_check_digit(base)


def font(n):
    for p in ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/segoeui.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "DejaVuSans.ttf", "Arial.ttf",
              "/System/Library/Fonts/Helvetica.ttc"):
        try: return ImageFont.truetype(p, n)
        except OSError: pass
    try: return ImageFont.load_default(size=n)   # Pillow >= 10.1 scales the fallback font
    except TypeError: return ImageFont.load_default()


def card(lines, name):
    img = Image.new("RGB", (900, 420), "white")
    d = ImageDraw.Draw(img)
    y = 30
    for text, size in lines:
        d.text((40, y), text, fill="black", font=font(size)); y += size + 22
    img.save(OUT / name)


a = f"{AADHAAR[:4]} {AADHAAR[4:8]} {AADHAAR[8:]}"
card([("Government of India", 28), ("Asha Kumari", 34), ("DOB: 14/08/2004", 30), ("Female", 30), (a, 44)], "sample_aadhaar.png")
card([("INCOME TAX DEPARTMENT", 28), ("Name", 24), ("ASHA KUMARI", 34), ("Date of Birth", 24), ("14/08/2004", 30), ("ABCPK1234F", 44)], "sample_pan.png")
print("Wrote sample_aadhaar.png and sample_pan.png (fake data). Aadhaar:", a)
