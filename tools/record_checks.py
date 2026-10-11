"""Repeat the application checks and preserve their actual results."""
import csv
import hashlib
import json
import platform
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence"
EVIDENCE.mkdir(exist_ok=True)
env = dict(__import__("os").environ, PYTHONUTF8="1")
test = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
                      cwd=ROOT, capture_output=True, encoding="utf-8", env=env)
(EVIDENCE / "tests.txt").write_text(test.stdout + test.stderr, encoding="utf-8")
if test.returncode:
    raise SystemExit("Tests failed. See evidence/tests.txt")
build = subprocess.run([sys.executable, "catalog.py", "data/products_demo.csv", "--output", "output"],
                       cwd=ROOT, capture_output=True, encoding="utf-8", env=env)
(EVIDENCE / "build.txt").write_text((build.stdout + build.stderr).replace(str(ROOT), "<project>"), encoding="utf-8")
if build.returncode:
    raise SystemExit("Build failed. See evidence/build.txt")


class Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids, self.references, self.skus = [], [], []
        self.scripts = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.ids.append(attrs["id"])
        if tag == "article":
            self.skus.append(attrs.get("data-sku"))
        self.scripts += tag == "script"
        for key in ("href", "src"):
            if key in attrs:
                self.references.append(attrs[key])


out = ROOT / "output"
report = json.loads((out / "report.json").read_text(encoding="utf-8"))
with (out / "normalized.csv").open(encoding="utf-8-sig", newline="") as handle:
    normalized = list(csv.DictReader(handle, delimiter=";"))
with (out / "issues.csv").open(encoding="utf-8-sig", newline="") as handle:
    issues = list(csv.DictReader(handle, delimiter=";"))
with (out / "changes.csv").open(encoding="utf-8-sig", newline="") as handle:
    changes = list(csv.DictReader(handle, delimiter=";"))
with (ROOT / "data/products_demo.csv").open(encoding="utf-8-sig", newline="") as handle:
    original = list(csv.reader(handle, delimiter=";"))
page = Page()
page.feed((out / "index.html").read_text(encoding="utf-8"))
checks = {
    "all_tests_passed": test.returncode == 0,
    "build_exit_zero": build.returncode == 0,
    "counts_reconcile": report["summary"]["input_records"] == len(normalized) + len({i["source_record"] for i in issues}),
    "report_issue_count_matches_csv": report["summary"]["issue_count"] == len(issues),
    "html_skus_match_normalized_in_order": page.skus == [p["sku"] for p in normalized],
    "ids_unique": len(page.ids) == len(set(page.ids)),
    "anchor_targets_exist": all(ref[1:] in page.ids for ref in page.references if ref.startswith("#")),
    "all_assets_and_download_targets_exist": all((out / ref).is_file() for ref in page.references if not ref.startswith("#")),
    "no_external_references": all(":" not in ref and not ref.startswith("//") for ref in page.references),
    "no_javascript": page.scripts == 0,
    "source_hash_matches": report["source_sha256"] == hashlib.sha256((ROOT / "data/products_demo.csv").read_bytes()).hexdigest(),
    "change_count_matches_csv": len(report["changes"]) == len(changes),
    "rejected_raw_values_preserved": all(r["values"] == original[r["source_record"] - 1] for r in report["rejected_records"]),
    "all_records_accounted_for": sorted(r["source_record"] for r in report["accepted_records"] + report["rejected_records"]) == list(range(2, len(original) + 1)),
}
# Independently reconstruct every accepted input field using the output and change log.
accepted_by_sku = {p["sku"]: p for p in normalized}
reconstructed = True
for accepted in report["accepted_records"]:
    record = accepted["source_record"]
    values = dict(accepted_by_sku[accepted["sku"]])
    for change in report["changes"]:
        if change["source_record"] == record:
            reconstructed &= values[change["field"]] == change["after"]
            values[change["field"]] = change["before"]
    reconstructed &= [values[name] for name in original[0]] == original[record - 1]
checks["accepted_input_reconstructed_from_change_log"] = reconstructed
for svg in (out / "assets").glob("*.svg"):
    ET.parse(svg)
checks["svg_xml_valid"] = True
evidence = {
    "checked_at_moscow": datetime.now(timezone(timedelta(hours=3))).isoformat(timespec="seconds"),
    "python": platform.python_version(), "platform": platform.platform(),
    "test_count": sum(line.startswith("test_") and line.endswith(" ... ok") for line in (test.stdout + test.stderr).splitlines()),
    "test_exit_code": test.returncode, "summary": report["summary"],
    "source_sha256": report["source_sha256"], "checks": checks,
    "normalization_changes": len(changes),
}
(EVIDENCE / "verification.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(evidence, ensure_ascii=False, indent=2))
raise SystemExit(0 if all(checks.values()) else 1)
