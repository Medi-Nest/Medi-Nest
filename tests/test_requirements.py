"""Keep the original functional requirements linked to executable tests."""
import ast
import re
import unittest
from pathlib import Path


class RequirementTests(unittest.TestCase):
    def test_functional_requirements_have_valid_test_references(self):
        root=Path(__file__).resolve().parents[1]
        specification=(root/'SRS.md').read_text(encoding='utf-8')
        requirements=set(re.findall(r'\*\*(REQ-[A-Z]+-\d+):\*\*',specification))
        rows=re.findall(r'^\| (REQ-[A-Z]+-\d+) \|.*?\| `(test_\w+)` \|',specification,re.MULTILINE)
        references=dict(rows)
        self.assertEqual(requirements,set(references))
        self.assertEqual(len(rows),len(references))
        syntax=ast.parse((root/'tests'/'test_booking.py').read_text(encoding='utf-8'))
        tests={node.name for node in ast.walk(syntax) if isinstance(node,ast.FunctionDef) and node.name.startswith('test_')}
        for requirement,method in references.items():
            with self.subTest(requirement=requirement):
                self.assertIn(method,tests)


if __name__=='__main__':unittest.main()
