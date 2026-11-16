"""Run the project's verification checks and produce inspectable evidence."""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import platform
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import trace
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDIA_TIME = timezone(timedelta(hours=5, minutes=30))


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class VerificationResult(unittest.TextTestResult):
    """Record one result per test, including failures inside subtests."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.records = []
        self.current = None

    def startTest(self, test):
        super().startTest(test)
        self.current = {"id": test.id(), "status": "PASSED", "details": ""}
        self.started = time.perf_counter()

    def stopTest(self, test):
        self.current["duration_seconds"] = round(time.perf_counter() - self.started, 6)
        self.records.append(self.current)
        super().stopTest(test)

    def addFailure(self, test, error):
        self.current.update(status="FAILED", details=self._exc_info_to_string(error, test))
        super().addFailure(test, error)

    def addError(self, test, error):
        self.current.update(status="ERROR", details=self._exc_info_to_string(error, test))
        super().addError(test, error)

    def addSkip(self, test, reason):
        self.current.update(status="SKIPPED", details=reason)
        super().addSkip(test, reason)

    def addExpectedFailure(self, test, error):
        self.current.update(status="SKIPPED", details="Expected failure: " + self._exc_info_to_string(error, test))
        super().addExpectedFailure(test, error)

    def addUnexpectedSuccess(self, test):
        self.current.update(status="FAILED", details="Unexpected success; review the expected-failure marker.")
        super().addUnexpectedSuccess(test)

    def addSubTest(self, test, subtest, error):
        if error is not None:
            status = "FAILED" if issubclass(error[0], test.failureException) else "ERROR"
            self.current.update(status=status, details=self._exc_info_to_string(error, subtest))
        super().addSubTest(test, subtest, error)


def flatten(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from flatten(item)
        else:
            yield item


def run_suite(exclude_performance=False):
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
    if exclude_performance:
        suite = unittest.TestSuite(test for test in flatten(suite)
                                   if not test.id().startswith("test_performance."))
    stream, output = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(output):
        result = unittest.TextTestRunner(stream=stream, verbosity=2,
                                        resultclass=VerificationResult).run(suite)
    return result, stream.getvalue() + "\n" + output.getvalue()


def run_command(name, command):
    started = time.perf_counter()
    try:
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                                timeout=120, check=False)
        return {"name": name, "command": command, "status": "PASSED" if result.returncode == 0 else "FAILED",
                "exit_code": result.returncode, "duration_seconds": round(time.perf_counter() - started, 6),
                "output": result.stdout + result.stderr}
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"name": name, "command": command, "status": "ERROR", "exit_code": None,
                "duration_seconds": round(time.perf_counter() - started, 6), "output": str(error)}


def git_value(*args):
    result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else "unavailable"


def source_fingerprint():
    paths = [ROOT / "run.py", ROOT / "pyproject.toml", ROOT / "SRS.md", Path(__file__).resolve()]
    for directory in (ROOT / "src", ROOT / "tests"):
        paths.extend(path for path in directory.rglob("*") if path.suffix in {".py", ".js", ".css", ".html", ".svg"})
    digest = hashlib.sha256()
    for path in sorted(set(paths)):
        digest.update(path.relative_to(ROOT).as_posix().encode() + b"\0")
        digest.update(path.read_text(encoding="utf-8").encode("utf-8") + b"\0")
    return digest.hexdigest()


def coverage_worker(output_dir):
    """Trace a fresh process, including HTTP and worker threads."""
    output_dir.mkdir(parents=True, exist_ok=True)
    tracer = trace.Trace(count=True, trace=False, ignoredirs=[sys.base_prefix, sys.prefix])
    previous_trace = threading.gettrace()
    threading.settrace(tracer.globaltrace)
    try:
        result, test_log = tracer.runfunc(run_suite, exclude_performance=True)
    finally:
        threading.settrace(previous_trace)
    application = (ROOT / "src" / "medicine_booking").resolve()
    counts = {(filename, line): count for (filename, line), count in tracer.results().counts.items()
              if Path(filename).resolve().is_relative_to(application)}
    summary = io.StringIO()
    with contextlib.redirect_stdout(summary):
        trace.CoverageResults(counts=counts).write_results(show_missing=True, summary=True,
                                                         coverdir=str(output_dir))
    modules = []
    for line in summary.getvalue().splitlines():
        match = re.match(r"\s*(\d+)\s+(\d+)%\s+(\S+)\s+\((.*)\)", line)
        if match:
            modules.append({"module": match[3], "executable_lines": int(match[1]), "line_coverage_percent": int(match[2])})
    (output_dir / "test-output.log").write_text(test_log, encoding="utf-8")
    (output_dir / "summary.json").write_text(json.dumps({"tests_run": result.testsRun,
        "successful": result.wasSuccessful(), "modules": modules}, indent=2) + "\n", encoding="utf-8")
    print(summary.getvalue())
    print(f"Coverage run: {result.testsRun} tests; successful={result.wasSuccessful()}.")
    return 0 if result.wasSuccessful() else 1


def write_junit(path, report):
    root = ET.Element("testsuites")
    cases = report["tests"]
    suite = ET.SubElement(root, "testsuite", name="MediNest application", tests=str(len(cases)),
                          failures=str(sum(c["status"] == "FAILED" for c in cases)),
                          errors=str(sum(c["status"] == "ERROR" for c in cases)),
                          skipped=str(sum(c["status"] == "SKIPPED" for c in cases)),
                          time=f"{sum(c['duration_seconds'] for c in cases):.6f}")
    for case in cases:
        classname, name = case["id"].rsplit(".", 1)
        node = ET.SubElement(suite, "testcase", classname=classname, name=name,
                             time=f"{case['duration_seconds']:.6f}")
        tag = {"FAILED": "failure", "ERROR": "error", "SKIPPED": "skipped"}.get(case["status"])
        if tag:
            ET.SubElement(node, tag).text = case["details"]
    checks = ET.SubElement(root, "testsuite", name="Verification checks", tests=str(len(report["checks"])))
    for check in report["checks"]:
        node = ET.SubElement(checks, "testcase", classname="verification", name=check["name"],
                             time=str(check["duration_seconds"]))
        if check["status"] != "PASSED":
            ET.SubElement(node, "skipped" if check["status"] == "SKIPPED" else "failure").text = check["output"]
    ET.indent(root)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def markdown_report(report):
    local_time = datetime.fromisoformat(report["finished_at"]).astimezone(INDIA_TIME).isoformat()
    tests = report["tests"]
    rows = ["# Verification Report", "", f"**Result:** {report['status']}", "",
            f"- Completed: `{local_time}` (Asia/Kolkata).",
            f"- Source baseline: `{report['revision']}` on `{report['branch']}`.",
            f"- Source fingerprint (SHA-256; normalized text): `{report['source_fingerprint']}`.",
            f"- Environment: Python {report['environment']['python']}, {report['environment']['system']}, SQLite {report['environment']['sqlite']}.",
            f"- Application tests: {len(tests)}; passed: {sum(t['status'] == 'PASSED' for t in tests)}; "
            f"failed/errors: {sum(t['status'] in {'FAILED', 'ERROR'} for t in tests)}; skipped: {sum(t['status'] == 'SKIPPED' for t in tests)}.",
            "", "The source baseline is the application commit at execution time. This report and its verification tooling are committed afterward; the fingerprint identifies the actual input files.",
            "", "## Automated checks", "", "| Check | Result | Duration (s) |", "|---|---|---:|"]
    rows += [f"| {check['name']} | {check['status']} | {check['duration_seconds']:.3f} |" for check in report["checks"]]
    rows += ["", "## Response-time measurements", "", "| Operation | Observed (ms) | SRS target (ms) |", "|---|---:|---:|"]
    metrics = report["timings_ms"]
    for label, key, target in [("Status read, p95", "status_p95", 1000), ("Booking submission, p95", "booking_p95", 2000),
                               ("Restock and queue processing", "restock", 3000)]:
        rows.append(f"| {label} | {metrics.get(key, 'Not measured')} | {target} |")
    rows += ["", "These measurements come from the uninstrumented performance test: 30 authenticated status reads, 20 sequential submissions, and one restock over the resulting small synthetic dataset. The p95 calculation uses nearest rank: ceil(0.95 * sample count). They establish local prototype behavior, not production capacity."]
    coverage = report.get("coverage")
    if coverage:
        rows += ["", "## Python line coverage", "", f"A separate traced run executed {coverage['tests_run']} non-performance tests. HTTP and allocation worker threads were included. Percentages are the standard-library tracer's rounded per-module line coverage; JavaScript and branch coverage are not measured.",
                 "", "| Module | Executable lines | Line coverage (%) |", "|---|---:|---:|"]
        rows += [f"| `{item['module']}` | {item['executable_lines']} | {item['line_coverage_percent']} |" for item in coverage["modules"]]
    rows += ["", "## Test-case results", "", "| Test case | Result | Duration (s) |", "|---|---|---:|"]
    rows += [f"| `{case['id']}` | {case['status']} | {case['duration_seconds']:.3f} |" for case in tests]
    rows += ["", "## Evidence and limits", "",
             "- Reproduce with `python scripts/verify.py --coverage --report docs/TEST_REPORT.md`.",
             "- Detailed results, JUnit XML, test output, and coverage files are generated under `build/verification/` and are excluded from Git. CI is configured to retain them as run artifacts.",
             "- Test databases contain synthetic fixtures and are isolated from the running application's data.",
             "- External provider failure is simulated; no real pharmacy, health-record, SMS, or email integration was exercised.",
             "- Desktop browser checks were recorded during implementation. Full mobile/accessibility review and user acceptance remain pending.",
             "- CI execution and branch publication require repository write access; this local run does not prove that GitHub CI has executed.", ""]
    return "\n".join(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "build" / "verification")
    parser.add_argument("--report", type=Path, help="Also write a reviewable Markdown report.")
    parser.add_argument("--coverage", action="store_true", help="Trace non-performance tests in a separate process.")
    parser.add_argument("--coverage-worker", action="store_true", help=argparse.SUPPRESS)
    options = parser.parse_args()
    output_dir = options.output_dir.resolve()
    if options.coverage_worker:
        return coverage_worker(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    started_at = timestamp()
    result, test_log = run_suite()
    (output_dir / "test-output.log").write_text(test_log, encoding="utf-8")
    checks = [run_command("Python compilation", [sys.executable, "-m", "compileall", "-q", "src", "run.py", "scripts"]),
              run_command("Git whitespace validation", ["git", "diff", "--check"])]
    if node := shutil.which("node"):
        checks.append(run_command("JavaScript syntax", [node, "--check", "src/medicine_booking/static/app.js"]))
    else:
        checks.append({"name": "JavaScript syntax", "status": "SKIPPED", "duration_seconds": 0,
                       "output": "Node.js is not installed.", "command": [], "exit_code": None})
    coverage = None
    if options.coverage:
        check = run_command("Python line coverage", [sys.executable, str(Path(__file__).resolve()),
                            "--coverage-worker", "--output-dir", str(output_dir / "coverage")])
        checks.append(check)
        coverage_path = output_dir / "coverage" / "summary.json"
        if check["status"] == "PASSED" and coverage_path.exists():
            coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
    match = re.search(r"status p95=([\d.]+) ms; booking p95=([\d.]+) ms; restock=([\d.]+) ms", test_log)
    timings = dict(zip(("status_p95", "booking_p95", "restock"), map(float, match.groups()))) if match else {}
    checks_failed = any(check["status"] in {"FAILED", "ERROR"} for check in checks)
    checks_skipped = any(check["status"] == "SKIPPED" for check in checks)
    status = "FAILED" if not result.wasSuccessful() or checks_failed else "INCOMPLETE" if checks_skipped else "PASSED"
    report = {"status": status, "started_at": started_at, "finished_at": timestamp(),
              "revision": git_value("rev-parse", "HEAD"), "branch": os.environ.get("GITHUB_REF_NAME") or git_value("branch", "--show-current"),
              "source_fingerprint": source_fingerprint(),
              "environment": {"python": platform.python_version(), "system": platform.system(), "sqlite": sqlite3.sqlite_version},
              "tests": result.records, "checks": checks, "timings_ms": timings, "coverage": coverage}
    (output_dir / "results.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    write_junit(output_dir / "junit.xml", report)
    if options.report:
        path = options.report if options.report.is_absolute() else ROOT / options.report
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(markdown_report(report), encoding="utf-8")
    print(f"Verification {report['status']}: {result.testsRun} application tests.")
    for check in checks:
        print(f"  {check['status']}: {check['name']}")
        if check["status"] not in {"PASSED", "SKIPPED"}:
            print(check["output"])
    if not result.wasSuccessful():
        print(test_log)
    print(f"Results: {output_dir}")
    return 0 if status == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
