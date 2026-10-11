"""Prepare local publishable files by allowlist. Never contacts GitHub."""
import argparse
import hashlib
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from catalog import ResourceReferences

DOCS = ("AUDIT.md", "CASE.md", "LEARNING_GUIDE.md", "PUBLISHING.md", "QA_REPORT.md",
        "PRE_RELEASE_FINDINGS.md", "PRE_RELEASE_AUDIT.md", "TEST_REVIEW.md", "ASSET_PROVENANCE.md")
TOOLS = ("record_checks.py", "prepare_release.py", "package_project.py", "build_pdf.py", "check_pdf.py",
         "prepare_screenshot_crops.py", "build_illustrations.py", "requirements-pdf.txt")
EVIDENCE = ("tests.txt", "build.txt", "verification.json", "browser-checks.json", "pdf-checks.json")
PRERELEASE = ("filesystem-before.json", "counts-before.json", "edge-cases-before.json", "tests-before.json",
              "privacy-before.json", "browser-before.json", "filesystem-after.json", "counts-after.json",
              "edge-cases-after.json", "tests-after.json", "privacy-after.json", "http-after.json")
SHOTS = ("desktop-top.jpg", "desktop-catalog.jpg", "desktop-report.jpg", "desktop-row.jpg",
         "mobile-top.jpg", "mobile-catalog.jpg", "mobile-report.jpg", "card-detail.jpg",
         "empty-catalog.jpg", "all-invalid.jpg", "audit-320.jpg", "audit-375.jpg", "audit-768.jpg",
         "audit-1024.jpg", "audit-1440.jpg", "audit-320-cards.jpg", "keyboard-focus.jpg")


def site_files():
    page = ResourceReferences()
    page.feed((ROOT / "output/index.html").read_text(encoding="utf-8"))
    # Placeholder is also included for documented non-demo SKUs.
    names = {"index.html", "assets/placeholder.svg", *page.files}
    paths = []
    for name in sorted(names):
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts or ":" in name:
            raise ValueError(f"Unsafe static reference: {name}")
        path = ROOT / "output" / relative
        if not path.is_file():
            raise ValueError(f"Missing static resource: {name}")
        # Windows is case-insensitive; GitHub Pages is not.
        parent = ROOT / "output"
        for part in relative.parts:
            if part not in {p.name for p in parent.iterdir()}:
                raise ValueError(f"Incorrect filename case: {name}")
            parent /= part
        paths.append(path)
    return paths


def repo_files():
    relative = [Path(p) for p in ("catalog.py", "README.md", ".gitignore", ".github/workflows/pages.yml",
                "tests/test_catalog.py", "data/products_demo.csv", "data/empty.csv", "data/all_invalid.csv",
                "templates/catalog.html", "templates/style.css", "output/pdf/portfolio_case.pdf")]
    relative += [Path("docs") / n for n in DOCS]
    relative += [Path("tools") / n for n in TOOLS]
    relative += [Path("evidence") / n for n in EVIDENCE]
    relative += [Path("evidence/prerelease") / n for n in PRERELEASE]
    relative += [Path("evidence/screenshots") / n for n in SHOTS]
    static = site_files()
    paths = [ROOT / r for r in relative] + static
    paths += [ROOT / "assets" / p.relative_to(ROOT / "output/assets")
              for p in static if (ROOT / "output/assets") in p.parents]
    for path in paths:
        if not path.is_file():
            raise ValueError(f"Missing release file: {path.relative_to(ROOT)}")
    return sorted(set(paths))


def scan_text(paths):
    """Known secret formats and private user paths; not an exhaustive DLP scanner."""
    patterns = {
        "private user path": r"[A-Z]:[\\/]+Users[\\/]",
        "private key": r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
        "token": r"(?:gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9]{20,}|AKIA[A-Z0-9]{16})",
        "email": r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
    }
    for path in paths:
        if path.suffix in (".jpg", ".png", ".pdf"):
            continue  # Binary metadata is checked separately during the audit.
        text = path.read_text(encoding="utf-8-sig")
        for kind, pattern in patterns.items():
            if re.search(pattern, text, flags=re.I if kind == "private user path" else 0):
                raise ValueError(f"Publication blocked ({kind}): {path.relative_to(ROOT)}")


def prepare(destination, site_only=False):
    paths = site_files() if site_only else repo_files()
    scan_text(paths)
    base = ROOT / "output" if site_only else ROOT
    names = {p.relative_to(base).as_posix() for p in paths} | {"SHA256SUMS.txt", ".nojekyll"}
    if destination.exists():
        unknown = [p for p in destination.rglob("*") if p.is_file()
                   and p.relative_to(destination).as_posix() not in names]
        if unknown:
            raise ValueError("Destination contains files outside the release allowlist; choose a new folder.")
    if destination.resolve() == ROOT or destination.resolve().is_relative_to(ROOT / "output"):
        raise ValueError("Release must be separate from working source/output.")
    for path in paths:
        target = destination / path.relative_to(base)
        if target.is_symlink() or target.resolve() != (destination.resolve() / path.relative_to(base)):
            raise ValueError("Release destination contains redirected paths.")
        if target.exists() and (not target.is_file() or target.stat().st_nlink > 1 or target.samefile(path)):
            raise ValueError("Release target is not an independent regular file.")
    destination.mkdir(parents=True, exist_ok=True)
    for path in paths:
        target = destination / path.relative_to(base)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    if site_only:
        (destination / ".nojekyll").write_text("", encoding="utf-8")
    published = sorted(p for p in destination.rglob("*") if p.is_file() and p.name != "SHA256SUMS.txt")
    (destination / "SHA256SUMS.txt").write_text("\n".join(
        f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(destination).as_posix()}"
        for p in published) + "\n", encoding="utf-8")
    print(f"Prepared {len(published)} files; no publication performed.")
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site-only", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.output, args.site_only)
