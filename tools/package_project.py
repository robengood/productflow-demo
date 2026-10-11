"""Prepare clean local repository/site ZIPs; verify extracted code and build."""
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from prepare_release import ROOT, prepare


def main():
    release = ROOT.parent / "release"
    repo = prepare(release / "productflow")
    site = prepare(release / "pages", site_only=True)
    archives = []
    for folder, name in [(repo, "productflow-github-ready.zip"), (site, "productflow-pages-ready.zip")]:
        archive_path = release / name
        paths = sorted(p for p in folder.rglob("*") if p.is_file())
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for p in paths:
                archive.write(p, (Path(folder.name) / p.relative_to(folder)).as_posix())
        with zipfile.ZipFile(archive_path) as archive:
            assert archive.testzip() is None, "ZIP CRC failed"
            assert all(archive.read((Path(folder.name)/p.relative_to(folder)).as_posix()) == p.read_bytes() for p in paths)
        archives.append({"archive": name, "files": len(paths), "bytes": archive_path.stat().st_size,
                         "sha256": hashlib.sha256(archive_path.read_bytes()).hexdigest()})
    (ROOT / "tmp").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="release-check-", dir=ROOT / "tmp") as temp:
        with zipfile.ZipFile(release / "productflow-github-ready.zip") as archive:
            assert all((Path(temp)/n).resolve().is_relative_to(Path(temp).resolve()) for n in archive.namelist())
            archive.extractall(temp)
        extracted = Path(temp) / "productflow"
        results = []
        for args in [["-m", "unittest", "discover", "-s", "tests", "-v"],
                     ["catalog.py", "data/products_demo.csv", "--output", "tmp/rebuilt"]]:
            run = subprocess.run([sys.executable, *args], cwd=extracted, capture_output=True,
                                 encoding="utf-8", env=dict(os.environ, PYTHONUTF8="1"))
            clean = (run.stdout + run.stderr).replace(str(extracted), "<extracted-project>")
            results.append({"command": "python " + " ".join(args), "exit_code": run.returncode, "output": clean})
            if run.returncode:
                raise RuntimeError(clean)
        rebuilt = extracted / "tmp/rebuilt"
        static_paths = [p for p in site.rglob("*") if p.is_file() and p.name not in (".nojekyll", "SHA256SUMS.txt")]
        matches = all((rebuilt/p.relative_to(site)).read_bytes() == p.read_bytes() for p in static_paths)
        if not matches:
            raise ValueError("Extracted build differs from release site")
        count = int(re.search(r"Ran (\d+) tests", results[0]["output"])[1])
    evidence = {"archives": archives, "extracted_tests": count, "extracted_build_matches_site": matches,
                "results": results, "published": False}
    (release / "release-verification.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({"archives": archives, "extracted_tests": count, "extracted_build_matches_site": matches,
                      "published": False}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
