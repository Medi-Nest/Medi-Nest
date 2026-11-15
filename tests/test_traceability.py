"""MBA-23: requirement traceability and structural checks (NFR-QA-06/09, NFR-SEC-14)."""

import ast
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import traceability  # noqa: E402


class TraceabilityTests(unittest.TestCase):
    def test_every_srs_requirement_is_traced(self):
        self.assertEqual(traceability.problems(), [])
        self.assertEqual(len(traceability.load_rows()), len(traceability.srs_requirements()))

    def test_generated_matrix_is_up_to_date(self):
        self.assertEqual(traceability.MD_PATH.read_text(encoding="utf-8"), traceability.render(),
                         "run: python tools/traceability.py")


class StructureTests(unittest.TestCase):
    def test_priority_policy_does_not_import_presentation(self):
        tree = ast.parse((ROOT / "src/medicine_booking/priority.py").read_text(encoding="utf-8"))
        imported = {node.module for node in ast.walk(tree)
                    if isinstance(node, ast.ImportFrom) and node.module}
        self.assertFalse(imported & {"patient_views", "manager_views", "notifications"})

    def test_secret_files_are_git_ignored(self):
        ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").split()
        for pattern in (".env", "*.pem", "*.key"):
            self.assertIn(pattern, ignored)


if __name__ == "__main__":
    unittest.main()
