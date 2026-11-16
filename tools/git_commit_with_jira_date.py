#!/usr/bin/env python3
"""
Git commit wrapper that sets GIT_AUTHOR_DATE and GIT_COMMITTER_DATE based on Jira ticket ID.

Usage:
    python tools/git_commit_with_jira_date.py -m "MBA-7: Implement prescription upload validation"
    python tools/git_commit_with_jira_date.py -m "MBA-10: Add e-prescription import [2024-10-25]"
    python tools/git_commit_with_jira_date.py --ticket MBA-15 -m "Build inventory reservation service"

The script:
1. Extracts Jira ticket ID from commit message (pattern: MBA-XX)
2. Looks up date from tools/jira_dates.json (uses end date by default)
3. Allows explicit date override with [YYYY-MM-DD] in message
4. Runs git commit with the appropriate dates set
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path


def load_jira_dates(json_path: Path) -> dict:
    """Load Jira ticket dates from JSON file."""
    if not json_path.exists():
        print(f"Warning: {json_path} not found. Using current date.", file=sys.stderr)
        return {}
    with open(json_path) as f:
        return json.load(f)


def extract_ticket_id(message: str) -> str | None:
    """Extract Jira ticket ID (MBA-XX) from commit message."""
    match = re.search(r'\b(MBA-\d+)\b', message, re.IGNORECASE)
    return match.group(1).upper() if match else None


def extract_explicit_date(message: str) -> str | None:
    """Extract explicit date from commit message in [YYYY-MM-DD] format."""
    match = re.search(r'\[(\d{4}-\d{2}-\d{2})\]', message)
    return match.group(1) if match else None


def get_commit_date(ticket_id: str, jira_dates: dict, explicit_date: str | None = None) -> str:
    """Determine the commit date to use."""
    if explicit_date:
        # If only date provided, add time
        if len(explicit_date) == 10:  # YYYY-MM-DD
            return explicit_date + " 12:00:00"
        return explicit_date

    if ticket_id in jira_dates:
        # Use end date of the ticket range
        date_str = jira_dates[ticket_id].get("end", "")
        if date_str and len(date_str) == 10:
            return date_str + " 12:00:00"
        return date_str

    # Fallback to current date
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def validate_date(date_str: str) -> bool:
    """Validate date format (YYYY-MM-DD or YYYY-MM-DD HH:MM:SS)."""
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
        try:
            datetime.strptime(date_str, fmt)
            return True
        except ValueError:
            continue
    return False


def main():
    parser = argparse.ArgumentParser(description="Git commit with Jira ticket date")
    parser.add_argument("-m", "--message", help="Commit message")
    parser.add_argument("--ticket", help="Explicit Jira ticket ID (e.g., MBA-7)")
    parser.add_argument("--date", help="Explicit date (YYYY-MM-DD)")
    parser.add_argument("--use-start-date", action="store_true", help="Use ticket start date instead of end date")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without committing")
    parser.add_argument("extra_args", nargs=argparse.REMAINDER, help="Additional git commit args")

    args = parser.parse_args()

    # Get commit message
    if args.message:
        commit_msg = args.message
    else:
        # Read from stdin or use git's default editor
        commit_msg = ""

    # Load Jira dates
    repo_root = Path(__file__).parent.parent
    jira_dates = load_jira_dates(repo_root / "tools" / "jira_dates.json")

    # Determine ticket ID
    ticket_id = args.ticket or extract_ticket_id(commit_msg)

    # Determine date
    explicit_date = args.date or extract_explicit_date(commit_msg)

    if ticket_id and ticket_id in jira_dates and args.use_start_date:
        commit_date = jira_dates[ticket_id].get("start", "")
    else:
        commit_date = get_commit_date(ticket_id, jira_dates, explicit_date)

    if not validate_date(commit_date):
        print(f"Error: Invalid date '{commit_date}'", file=sys.stderr)
        sys.exit(1)

    # Show what we're doing
    if ticket_id:
        print(f"Jira ticket: {ticket_id}")
        if ticket_id in jira_dates:
            desc = jira_dates[ticket_id].get("description", "")
            print(f"Description: {desc}")
    print(f"Commit date: {commit_date}")
    print(f"Commit message: {commit_msg}")

    if args.dry_run:
        print("DRY RUN - not committing")
        return

    # Set environment variables and run git commit
    env = os.environ.copy()
    env["GIT_AUTHOR_DATE"] = commit_date
    env["GIT_COMMITTER_DATE"] = commit_date

    # Build git commit command
    cmd = ["git", "commit"]
    if commit_msg:
        cmd.extend(["-m", commit_msg])
    cmd.extend(args.extra_args)

    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, env=env)
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()