# Source: https://github.com/amulya-labs/ai-dev-foundry
# License: MIT (https://opensource.org/licenses/MIT)
"""Tests for scripts/review-stats.py"""

import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Add scripts dir to path so we can import
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import importlib
review_stats = importlib.import_module("review-stats")


# ---------------------------------------------------------------------------
# Test data
# ---------------------------------------------------------------------------

SAMPLE_FINDINGS = [
    {
        "file": "src/auth.py",
        "line": 42,
        "severity": "Critical",
        "comment": "SQL injection via unsanitized user input",
        "suggestion": "Use parameterized queries",
    },
    {
        "file": "src/auth.py",
        "line": 87,
        "severity": "High",
        "comment": "Password stored in plaintext",
        "suggestion": "Use bcrypt hashing",
    },
    {
        "file": "src/api.py",
        "line": 15,
        "severity": "Medium",
        "comment": "Missing rate limiting on login endpoint",
        "suggestion": "",
    },
    {
        "file": "src/utils.py",
        "line": 33,
        "severity": "Low",
        "comment": "Unused import",
        "suggestion": "Remove unused import",
    },
    {
        "file": "src/api.py",
        "line": 102,
        "severity": "High",
        "comment": "Unvalidated redirect URL",
        "suggestion": "Validate against allowlist",
    },
]

SAMPLE_ARTIFACTS = [
    {
        "run_id": 1001,
        "created_at": "2026-03-01T10:00:00Z",
        "findings": SAMPLE_FINDINGS[:3],
    },
    {
        "run_id": 1002,
        "created_at": "2026-03-08T14:30:00Z",
        "findings": SAMPLE_FINDINGS[3:],
    },
]


# ---------------------------------------------------------------------------
# compute_stats tests
# ---------------------------------------------------------------------------

class TestComputeStats:
    def test_total_counts(self):
        stats = review_stats.compute_stats(SAMPLE_ARTIFACTS)
        assert stats["total_reviews"] == 2
        assert stats["total_findings"] == 5

    def test_severity_counts(self):
        stats = review_stats.compute_stats(SAMPLE_ARTIFACTS)
        assert stats["severity_counts"]["Critical"] == 1
        assert stats["severity_counts"]["High"] == 2
        assert stats["severity_counts"]["Medium"] == 1
        assert stats["severity_counts"]["Low"] == 1

    def test_top_files(self):
        stats = review_stats.compute_stats(SAMPLE_ARTIFACTS)
        top = stats["top_files"]
        # src/auth.py and src/api.py both have 2 findings
        file_names = [t["file"] for t in top]
        assert "src/auth.py" in file_names
        assert "src/api.py" in file_names

    def test_hotspots(self):
        stats = review_stats.compute_stats(SAMPLE_ARTIFACTS)
        hotspots = stats["hotspots"]
        assert len(hotspots) > 0
        # src/auth.py has Critical + High, should be first
        assert hotspots[0]["file"] == "src/auth.py"
        assert hotspots[0]["critical"] == 1
        assert hotspots[0]["high"] == 1

    def test_empty_artifacts(self):
        stats = review_stats.compute_stats([])
        assert stats["total_reviews"] == 0
        assert stats["total_findings"] == 0
        assert stats["severity_counts"] == {}
        assert stats["top_files"] == []
        assert stats["hotspots"] == []

    def test_weekly_trend(self):
        stats = review_stats.compute_stats(SAMPLE_ARTIFACTS)
        assert len(stats["findings_by_week"]) > 0

    def test_single_finding(self):
        single = [{
            "run_id": 9999,
            "created_at": "2026-03-10T00:00:00Z",
            "findings": [SAMPLE_FINDINGS[0]],
        }]
        stats = review_stats.compute_stats(single)
        assert stats["total_reviews"] == 1
        assert stats["total_findings"] == 1
        assert stats["severity_counts"] == {"Critical": 1}


# ---------------------------------------------------------------------------
# Output formatting tests
# ---------------------------------------------------------------------------

class TestFormatting:
    def test_format_json_valid(self):
        stats = review_stats.compute_stats(SAMPLE_ARTIFACTS)
        output = review_stats.format_json(stats)
        parsed = json.loads(output)
        assert parsed["total_reviews"] == 2

    def test_format_table_contains_header(self):
        stats = review_stats.compute_stats(SAMPLE_ARTIFACTS)
        output = review_stats.format_table(stats)
        assert "Gemini Code Review Statistics" in output

    def test_format_table_contains_severity(self):
        stats = review_stats.compute_stats(SAMPLE_ARTIFACTS)
        output = review_stats.format_table(stats)
        assert "Critical" in output
        assert "High" in output

    def test_format_table_empty_stats(self):
        stats = review_stats.compute_stats([])
        output = review_stats.format_table(stats)
        assert "Total findings: 0" in output


# ---------------------------------------------------------------------------
# CLI argument parsing tests
# ---------------------------------------------------------------------------

class TestParseArgs:
    def test_defaults(self):
        with patch("sys.argv", ["review-stats.py"]):
            args = review_stats.parse_args()
            assert args.days == 30
            assert args.output_format == "table"

    def test_custom_days(self):
        with patch("sys.argv", ["review-stats.py", "--days", "7"]):
            args = review_stats.parse_args()
            assert args.days == 7

    def test_json_format(self):
        with patch("sys.argv", ["review-stats.py", "--format", "json"]):
            args = review_stats.parse_args()
            assert args.output_format == "json"

    def test_repo_from_env(self):
        with patch.dict("os.environ", {"GITHUB_REPOSITORY": "test/repo"}):
            with patch("sys.argv", ["review-stats.py"]):
                args = review_stats.parse_args()
                assert args.repo == "test/repo"
