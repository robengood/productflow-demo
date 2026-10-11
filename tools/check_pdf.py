"""Inspect the finished PDF; visual review remains a separate human/agent step."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "output/pdf/portfolio_case.pdf"
reader = PdfReader(path)
texts = [page.extract_text() for page in reader.pages]
verification = json.loads((ROOT / "evidence/verification.json").read_text(encoding="utf-8"))
checks = {
    "three_pages": len(reader.pages) == 3,
    "project_and_disclosure": all(s in texts[0] for s in ("ProductFlow", "Учебный проект", "синтетические данные", "AI")),
    "data_examples": all(s in texts[1] for s in ("DS-001", "1290.00", "DUP-001", "BAD-002", "BAD-003", "-100", "2.5")),
    "current_test_count": str(verification["test_count"]) in texts[2],
    "limitations_present": "CMS" in texts[2] and "не проверялись" in texts[2],
    "images_on_every_page": all(len(page.images) > 0 for page in reader.pages),
}
result = {
    "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "pages": len(reader.pages), "bytes": path.stat().st_size,
    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    "checks": checks,
    "visual_review": "This script does not assess layout. The separately performed visual review is documented in docs/QA_REPORT.md.",
}
(ROOT / "evidence/pdf-checks.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(result, ensure_ascii=False, indent=2))
raise SystemExit(0 if all(checks.values()) else 1)
