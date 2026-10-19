"""Run v2 regressions and retain a machine-readable evidence report."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class EvidenceResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.successes = []

    def addSuccess(self, test):
        super().addSuccess(test)
        self.successes.append(test.id())


if __name__ == "__main__":
    started = datetime.now(timezone.utc)
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern="test_core_*.py")
    result = unittest.TextTestRunner(verbosity=2, resultclass=EvidenceResult).run(suite)
    directory = ROOT / ".verification-data" / ("core-report-" + uuid.uuid4().hex)
    directory.mkdir(parents=True, exist_ok=False)
    report = {"started_utc": started.isoformat(), "finished_utc": datetime.now(timezone.utc).isoformat(),
              "tests_run": result.testsRun, "successful": result.wasSuccessful(),
              "passed": result.successes, "failures": [{"test": test.id(), "trace": trace} for test, trace in result.failures],
              "errors": [{"test": test.id(), "trace": trace} for test, trace in result.errors],
              "limitations": ["SQLite only; PostgreSQL concurrency remains unverified", "No physical barcode printer or scanner test", "Synthetic isolated data only"]}
    with (directory / "result.json").open("x", encoding="utf-8") as output:
        json.dump(report, output, ensure_ascii=False, indent=2)
    print("Evidence:", directory / "result.json")
    sys.exit(0 if result.wasSuccessful() else 1)
