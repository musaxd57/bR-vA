"""Export real application SVGs for an independent raster/ZXing acceptance test.

This script neither imports nor reproduces the encoder's pattern/checksum
tables. Expected decoder text is the original input string. It opens no app,
session, database, network connection or production file.

Run from the repository root:
    .venv-local/Scripts/python.exe tests/workbench_label_fixtures.py
    node --test --test-isolation=none tests/workbench_label_decode.cjs
"""
from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Import the real renderer without executing briva_core.__init__ and loading
# the web application's ORM. Its sibling errors.py remains the real module.
package = types.ModuleType("_briva_label_encoder_under_test")
package.__path__ = [str(ROOT / "briva_core")]
sys.modules[package.__name__] = package
code128_svg = importlib.import_module(package.__name__ + ".labels").code128_svg


def create_fixtures(output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    cases = [
        ("unit-default", "BRVU000000000001", True),
        ("recipe-default", "BRVR000000000001", True),
        ("package-default", "BRVP000000000001", True),
        ("location-default", "BRVL000000000001", True),
        ("numeric-13", "2001000000014", True),
        ("leading-zeros", "0001000000014", True),
        ("all-zeros", "0000000000000", False),
        ("prefix-max-generated", "BRIVAB2BU000000000001", True),
        ("shortest", "A", False),
        ("escaped-ascii", "BRV<&>\"' +/%", False),
        ("max-48", "BRVU" + "0123456789" * 4 + "ABCD", False),
        ("ascii-first-half", "".join(chr(value) for value in range(32, 80)), False),
        ("ascii-second-half", "".join(chr(value) for value in range(80, 127)), False),
    ]
    manifest = {"encoder": "briva_core.labels.code128_svg", "encoder_sha256": hashlib.sha256((ROOT / "briva_core" / "labels.py").read_bytes()).hexdigest(), "cases": [], "invalid_inputs": []}
    for case_id, code, production_shape in cases:
        svg = code128_svg(code)
        filename = case_id + ".svg"
        (output / filename).write_text(svg, encoding="utf-8")
        manifest["cases"].append({
            "id": case_id, "code": code, "file": filename,
            "production_shape": production_shape, "length": len(code),
            "svg_sha256": hashlib.sha256(svg.encode("utf-8")).hexdigest(),
        })
    for case_id, code in [("empty", ""), ("newline", "BRV\n123"), ("unicode", "BRVş123"), ("too-long", "A" * 49)]:
        rejected = False
        try:
            code128_svg(code)
        except Exception as error:
            rejected = True
            error_type = type(error).__name__
        if not rejected:
            raise AssertionError(f"Invalid fixture unexpectedly accepted: {case_id}")
        manifest["invalid_inputs"].append({"id": case_id, "rejected": True, "error_type": error_type})
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


if __name__ == "__main__":
    output = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT / ".verification-data" / "label-independent-20261019"
    # Test output stays in the repository's explicit verification directory.
    if not output.is_relative_to(ROOT / ".verification-data"):
        raise SystemExit("Fixture output must stay under .verification-data/")
    result = create_fixtures(output)
    print(json.dumps({"output": str(output), "valid_svgs": len(result["cases"]), "invalid_inputs_rejected": len(result["invalid_inputs"])}, ensure_ascii=False))
