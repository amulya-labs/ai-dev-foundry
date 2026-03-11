# Source: https://github.com/amulya-labs/ai-dev-foundry
# License: MIT (https://opensource.org/licenses/MIT)
"""
review-stats.py -- Collect and display Gemini code review statistics.

Parses inline-comments JSON artifacts from past reviews and produces
a summary report of findings by severity, file, and trend over time.

Usage:
  python3 scripts/review-stats.py [--repo OWNER/REPO] [--days N] [--format json|table]
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_DAYS = 30
MAX_ARTIFACTS = 100
SEVERITY_ORDER = ["Critical", "High", "Medium", "Low"]
SEVERITY_COLORS = {
    "Critical": "\033[91m",  # red
    "High": "\033[93m",      # yellow
    "Medium": "\033[96m",    # cyan
    "Low": "\033[37m",       # white
}
RESET = "\033[0m"


# ---------------------------------------------------------------------------
# Data collection
# ---------------------------------------------------------------------------

def fetch_review_artifacts(repo: str, days: int) -> list[dict]:
    """
    Fetch inline-comments artifacts from GitHub Actions runs.
    Returns a list of parsed JSON arrays (one per review run).
    """
    since = datetime.now() - timedelta(days=days)
    since_str = since.strftime("%Y-%m-%d")

    # List workflow runs for gemini-code-review
    cmd = [
        "gh", "run", "list",
        "--repo", repo,
        "--workflow", "gemini-code-review.yml",
        "--created", f">={since_str}",
        "--limit", str(MAX_ARTIFACTS),
        "--json", "databaseId,conclusion,createdAt",
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        runs = json.loads(result.stdout)
    except (subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        print(f"WARNING: Failed to list runs: {exc}", file=sys.stderr)
        return []

    artifacts = []
    for run in runs:
        run_id = run["databaseId"]
        created = run["createdAt"]

        # Download inline-comments artifact
        with tempfile.TemporaryDirectory() as tmpdir:
            dl_cmd = [
                "gh", "run", "download", str(run_id),
                "--repo", repo,
                "--name", f"inline-comments-oss-*",
                "--dir", tmpdir,
            ]
            try:
                subprocess.run(dl_cmd, capture_output=True, text=True, check=True,
                               timeout=30)
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                continue

            # Parse all JSON files in the download
            for json_file in Path(tmpdir).rglob("*.json"):
                try:
                    data = json.loads(json_file.read_text())
                    if isinstance(data, list) and len(data) > 0:
                        artifacts.append({
                            "run_id": run_id,
                            "created_at": created,
                            "findings": data,
                        })
                except (json.JSONDecodeError, OSError):
                    pass

    return artifacts


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def compute_stats(artifacts: list[dict]) -> dict:
    """Compute aggregate statistics from review artifacts."""
    total_reviews = len(artifacts)
    total_findings = 0
    severity_counts = Counter()
    file_counts = Counter()
    findings_by_week = defaultdict(int)
    severity_by_file = defaultdict(lambda: Counter())

    for artifact in artifacts:
        findings = artifact["findings"]
        total_findings += len(findings)
        created = artifact["created_at"]

        # Parse week key
        try:
            dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
            week_key = dt.strftime("%Y-W%U")
        except (ValueError, AttributeError):
            week_key = "unknown"

        for finding in findings:
            severity = finding.get("severity", "Unknown")
            file_path = finding.get("file", "unknown")

            severity_counts[severity] += 1
            file_counts[file_path] += 1
            findings_by_week[week_key] += 1
            severity_by_file[file_path][severity] += 1

    # Top offending files
    top_files = file_counts.most_common(10)

    # Hotspots: files with Critical or High findings
    hotspots = []
    for file_path, sev_counter in severity_by_file.items():
        critical = sev_counter.get("Critical", 0)
        high = sev_counter.get("High", 0)
        if critical > 0 or high > 0:
            hotspots.append({
                "file": file_path,
                "critical": critical,
                "high": high,
                "total": sum(sev_counter.values()),
            })
    hotspots.sort(key=lambda x: (x["critical"], x["high"]), reverse=True)

    return {
        "total_reviews": total_reviews,
        "total_findings": total_findings,
        "severity_counts": dict(severity_counts),
        "top_files": [{"file": f, "count": c} for f, c in top_files],
        "hotspots": hotspots[:10],
        "findings_by_week": dict(sorted(findings_by_week.items())),
    }


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

def format_table(stats: dict) -> str:
    """Format stats as a human-readable table."""
    lines = []
    lines.append("=" * 60)
    lines.append("  Gemini Code Review Statistics")
    lines.append("=" * 60)
    lines.append("")
    lines.append(f"  Total reviews:  {stats['total_reviews']}")
    lines.append(f"  Total findings: {stats['total_findings']}")
    lines.append("")

    # Severity breakdown
    lines.append("  Severity Breakdown:")
    lines.append("  " + "-" * 40)
    for sev in SEVERITY_ORDER:
        count = stats["severity_counts"].get(sev, 0)
        color = SEVERITY_COLORS.get(sev, "")
        bar = "#" * min(count, 40)
        lines.append(f"  {color}{sev:10s}{RESET}  {count:4d}  {bar}")
    lines.append("")

    # Top files
    if stats["top_files"]:
        lines.append("  Most Reviewed Files:")
        lines.append("  " + "-" * 40)
        for entry in stats["top_files"]:
            lines.append(f"  {entry['count']:4d}  {entry['file']}")
        lines.append("")

    # Hotspots
    if stats["hotspots"]:
        lines.append("  Hotspots (Critical/High findings):")
        lines.append("  " + "-" * 40)
        for hs in stats["hotspots"]:
            lines.append(
                f"  {hs['file']}  "
                f"(C:{hs['critical']} H:{hs['high']} total:{hs['total']})"
            )
        lines.append("")

    # Weekly trend
    if stats["findings_by_week"]:
        lines.append("  Weekly Trend:")
        lines.append("  " + "-" * 40)
        for week, count in stats["findings_by_week"].items():
            bar = "█" * min(count, 30)
            lines.append(f"  {week}  {count:3d}  {bar}")
        lines.append("")

    lines.append("=" * 60)
    return "\n".join(lines)


def format_json(stats: dict) -> str:
    """Format stats as JSON."""
    return json.dumps(stats, indent=2)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect and display Gemini code review statistics"
    )
    parser.add_argument(
        "--repo",
        default=os.environ.get("GITHUB_REPOSITORY", ""),
        help="GitHub repo (owner/repo). Defaults to GITHUB_REPOSITORY env var.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=DEFAULT_DAYS,
        help=f"Number of days to look back (default: {DEFAULT_DAYS})",
    )
    parser.add_argument(
        "--format",
        choices=["json", "table"],
        default="table",
        dest="output_format",
        help="Output format (default: table)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if not args.repo:
        print("ERROR: --repo is required (or set GITHUB_REPOSITORY)", file=sys.stderr)
        sys.exit(1)

    print(f"Fetching review data for {args.repo} (last {args.days} days)...",
          file=sys.stderr)

    artifacts = fetch_review_artifacts(args.repo, args.days)

    if not artifacts:
        print("No review artifacts found.", file=sys.stderr)
        # Still output valid empty stats
        stats = {
            "total_reviews": 0,
            "total_findings": 0,
            "severity_counts": {},
            "top_files": [],
            "hotspots": [],
            "findings_by_week": {},
        }
    else:
        stats = compute_stats(artifacts)

    if args.output_format == "json":
        print(format_json(stats))
    else:
        print(format_table(stats))


if __name__ == "__main__":
    main()
