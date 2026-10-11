"""Crop real full-page browser captures; do not alter their content."""
import json
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SHOTS = ROOT / "evidence/screenshots"
evidence = json.loads((ROOT / "evidence/browser-checks.json").read_text(encoding="utf-8"))

if evidence.get("captureMode") == "viewport":
    import shutil
    shutil.copyfile(SHOTS / "audit-1440.jpg", SHOTS / "desktop-top.jpg")
    # Crop only the real captured pixels, without reconstructing a full page.
    image = Image.open(SHOTS / "desktop-catalog.jpg")
    image.crop((112, 16, min(1312, image.width), min(463, image.height))).save(SHOTS / "desktop-row.jpg", quality=95)
    card = evidence["mobileGeometry"]["card"]
    image = Image.open(SHOTS / "mobile-catalog.jpg")
    image.crop((int(card["left"]), int(card["top"])-1, int(card["right"]), int(card["bottom"])+1)).save(SHOTS / "card-detail.jpg", quality=95)
    print("Crops of current viewport captures saved; older full-page files are not used.")
    raise SystemExit(0)

def crop(source, name, y, height, x=0, width=None):
    image = Image.open(SHOTS / source)
    width = width or image.width
    y = max(0, int(y))
    image.crop((x, y, x + width, min(y + height, image.height))).save(SHOTS / name, quality=95)

desktop = evidence["desktopGeometry"]
mobile = evidence["mobileGeometry"]
crop("desktop.jpg", "desktop-top.jpg", 0, 1000)
crop("desktop.jpg", "desktop-catalog.jpg", desktop["catalog"], 1000)
crop("desktop-expanded.jpg", "desktop-report.jpg", desktop["report"], 1000)
crop("desktop.jpg", "desktop-row.jpg", desktop["organization"] - 8, 447, 112, 1200)
crop("mobile-full.jpg", "mobile-top.jpg", 0, 844)
crop("mobile-full.jpg", "mobile-catalog.jpg", mobile["organization"] - 10, 844)
crop("mobile-full.jpg", "mobile-report.jpg", mobile["report"], 844)
crop("mobile-full.jpg", "card-detail.jpg", mobile["firstCard"] - 1, 391, 20, 335)
print("Real screenshot crops saved; full-page originals retained.")
