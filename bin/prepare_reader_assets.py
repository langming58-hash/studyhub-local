#!/usr/bin/env python3
"""Copy exact audited Reader assets into the generated static tree."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "static" / "vendor" / "reader"
ASSETS = {
    "pdfjs/pdf.mjs": "node_modules/pdfjs-dist/legacy/build/pdf.mjs",
    "pdfjs/pdf.worker.mjs": "node_modules/pdfjs-dist/legacy/build/pdf.worker.mjs",
    "prism/prism.js": "node_modules/prismjs/prism.js",
    "prism/prism.css": "node_modules/prismjs/themes/prism.css",
    "prism/prism-python.min.js": "node_modules/prismjs/components/prism-python.min.js",
    "prism/prism-r.min.js": "node_modules/prismjs/components/prism-r.min.js",
    "prism/prism-json.min.js": "node_modules/prismjs/components/prism-json.min.js",
    "prism/prism-markdown.min.js": "node_modules/prismjs/components/prism-markdown.min.js",
    "licenses/PDFJS-LICENSE.txt": "node_modules/pdfjs-dist/LICENSE",
    "licenses/PRISM-LICENSE.txt": "node_modules/prismjs/LICENSE",
}


def version(name: str) -> str:
    return json.loads((ROOT / "node_modules" / name / "package.json").read_text(encoding="utf-8"))["version"]


def main() -> int:
    expected = {"pdfjs-dist": "6.4.299", "prismjs": "1.30.0"}
    for package, wanted in expected.items():
        actual = version(package)
        if actual != wanted:
            raise SystemExit(f"Reader dependency drift: {package} expected {wanted}, found {actual}")
    if TARGET.exists():
        shutil.rmtree(TARGET)
    for destination, source in ASSETS.items():
        src = ROOT / source
        if not src.is_file():
            raise SystemExit(f"Missing Reader asset: {source}")
        target = TARGET / destination
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
    (TARGET / "versions.json").write_text(json.dumps(expected, sort_keys=True) + "\n", encoding="utf-8")
    print("Reader assets: ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
