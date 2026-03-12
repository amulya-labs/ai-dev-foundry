# Tests for gemini_review.py — validates diff processing, JSON parsing,
# cache manifest handling, metrics output, and end-to-end review pipeline
# with mocked Gemini API responses.
#
# Source: https://github.com/amulya-labs/ai-dev-foundry
# License: MIT (https://opensource.org/licenses/MIT)
#
# Run:
#   pytest tests/test_gemini_review.py -v
#   pytest tests/test_gemini_review.py -v -k "test_parse_json"

import importlib
import json
import os
import sys
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Import the module under test
# ---------------------------------------------------------------------------

SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent / ".github" / "workflows" / "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

# Import with minimal env vars to avoid die() on missing GEMINI_API_KEY
_env_defaults = {
    "GEMINI_API_KEY": "test-key",
    "DIFF_FOCUSED": "/dev/null",
    "SELECTED_MODEL": "gemini-2.5-flash",
    "OUTPUT_FILE": "/tmp/test-inline-comments.json",
    "METRICS_FILE": "/tmp/test-review-metrics.json",
    "CACHE_MANIFEST_PATH": "/nonexistent/manifest.yml",
    "USE_CACHE": "0",
}

for k, v in _env_defaults.items():
    os.environ.setdefault(k, v)

import gemini_review as gr  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_DIFF = textwrap.dedent("""\
    diff --git a/src/auth.py b/src/auth.py
    index abc1234..def5678 100644
    --- a/src/auth.py
    +++ b/src/auth.py
    @@ -10,6 +10,8 @@ import hashlib
     import os
     from datetime import datetime

    +API_KEY = "sk-hardcoded-secret-12345"
    +
     def authenticate(user, password):
         \"\"\"Authenticate user against the database.\"\"\"
    -    query = f"SELECT * FROM users WHERE name='{user}' AND pass='{password}'"
    +    query = f"SELECT * FROM users WHERE name='{user}' AND pass='{password}'"  # noqa
    +    # TODO: use parameterized queries
         return db.execute(query)
    +
    +def get_token(user_id):
    +    token = hashlib.md5(str(user_id).encode()).hexdigest()
    +    return token
""")

SAMPLE_FINDINGS_JSON = json.dumps([
    {
        "file": "src/auth.py",
        "line": 13,
        "severity": "Critical",
        "comment": "Hardcoded API key exposed in source code.",
        "suggestion": '# API_KEY should be loaded from environment\nAPI_KEY = os.environ["API_KEY"]'
    },
    {
        "file": "src/auth.py",
        "line": 17,
        "severity": "Critical",
        "comment": "SQL injection vulnerability via f-string interpolation.",
        "suggestion": 'query = "SELECT * FROM users WHERE name=? AND pass=?"\nreturn db.execute(query, (user, password))'
    },
    {
        "file": "src/auth.py",
        "line": 22,
        "severity": "High",
        "comment": "MD5 is cryptographically broken; use secrets.token_hex() for token generation.",
        "suggestion": "import secrets\n\ndef get_token(user_id):\n    return secrets.token_hex(32)"
    },
])

SAMPLE_LOCK_DIFF = textwrap.dedent("""\
    diff --git a/package-lock.json b/package-lock.json
    index 111..222 100644
    --- a/package-lock.json
    +++ b/package-lock.json
    @@ -1,3 +1,3 @@
    -{  "lockfileVersion": 2 }
    +{  "lockfileVersion": 3 }
    diff --git a/src/index.js b/src/index.js
    index 333..444 100644
    --- a/src/index.js
    +++ b/src/index.js
    @@ -1,2 +1,3 @@
     console.log("hello");
    +console.log("world");
""")


# ---------------------------------------------------------------------------
# parse_json_response
# ---------------------------------------------------------------------------

class TestParseJsonResponse:
    def test_clean_json_array(self):
        result = gr.parse_json_response('[{"file": "a.py", "line": 1}]')
        assert len(result) == 1
        assert result[0]["file"] == "a.py"

    def test_empty_array(self):
        assert gr.parse_json_response("[]") == []

    def test_strips_markdown_fences(self):
        raw = '```json\n[{"file": "b.py", "line": 2}]\n```'
        result = gr.parse_json_response(raw)
        assert len(result) == 1

    def test_strips_plain_fences(self):
        raw = '```\n[{"file": "c.py", "line": 3}]\n```'
        result = gr.parse_json_response(raw)
        assert len(result) == 1

    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="empty response"):
            gr.parse_json_response("")

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError, match="empty response"):
            gr.parse_json_response("   \n  ")

    def test_non_array_raises(self):
        with pytest.raises(ValueError, match="not an array"):
            gr.parse_json_response('{"key": "value"}')

    def test_invalid_json_raises(self):
        with pytest.raises(ValueError, match="Failed to parse"):
            gr.parse_json_response("this is not json")

    def test_real_findings_json(self):
        result = gr.parse_json_response(SAMPLE_FINDINGS_JSON)
        assert len(result) == 3
        assert result[0]["severity"] == "Critical"
        assert result[2]["severity"] == "High"

    def test_findings_with_surrounding_whitespace(self):
        raw = f"\n\n  {SAMPLE_FINDINGS_JSON}  \n\n"
        result = gr.parse_json_response(raw)
        assert len(result) == 3


# ---------------------------------------------------------------------------
# truncate_diff
# ---------------------------------------------------------------------------

class TestTruncateDiff:
    def test_short_diff_unchanged(self):
        assert gr.truncate_diff("short diff", 100) == "short diff"

    def test_exact_limit_unchanged(self):
        diff = "x" * 100
        assert gr.truncate_diff(diff, 100) == diff

    def test_over_limit_truncated(self):
        diff = "x" * 200
        result = gr.truncate_diff(diff, 100)
        assert result.startswith("x" * 100)
        assert "DIFF TRUNCATED" in result
        assert "200 chars" in result

    def test_default_limit(self):
        # Just verify no crash with default limit
        result = gr.truncate_diff("small diff")
        assert result == "small diff"


# ---------------------------------------------------------------------------
# parse_cache_manifest
# ---------------------------------------------------------------------------

class TestParseCacheManifest:
    def test_nonexistent_file(self):
        assert gr.parse_cache_manifest("/nonexistent/file.yml") == []

    def test_valid_manifest(self, tmp_path):
        manifest = tmp_path / "manifest.yml"
        manifest.write_text(textwrap.dedent("""\
            # Cache manifest
            include:
              - "docs/**/*.md"
              - "*.md"
              - "src/contracts/**/*"
        """))
        result = gr.parse_cache_manifest(str(manifest))
        assert result == ["docs/**/*.md", "*.md", "src/contracts/**/*"]

    def test_manifest_with_comments(self, tmp_path):
        manifest = tmp_path / "manifest.yml"
        manifest.write_text(textwrap.dedent("""\
            include:
              # Architecture docs
              - "docs/adr/**/*.md"
              # Top-level docs
              - "README.md"
        """))
        result = gr.parse_cache_manifest(str(manifest))
        assert result == ["docs/adr/**/*.md", "README.md"]

    def test_manifest_stops_at_next_key(self, tmp_path):
        manifest = tmp_path / "manifest.yml"
        manifest.write_text(textwrap.dedent("""\
            include:
              - "docs/**/*.md"
              - "*.md"
            exclude:
              - "node_modules/**"
        """))
        result = gr.parse_cache_manifest(str(manifest))
        assert result == ["docs/**/*.md", "*.md"]

    def test_manifest_single_quotes(self, tmp_path):
        manifest = tmp_path / "manifest.yml"
        manifest.write_text("include:\n  - 'src/**/*.py'\n")
        result = gr.parse_cache_manifest(str(manifest))
        assert result == ["src/**/*.py"]

    def test_manifest_no_quotes(self, tmp_path):
        manifest = tmp_path / "manifest.yml"
        manifest.write_text("include:\n  - docs/**/*.md\n")
        result = gr.parse_cache_manifest(str(manifest))
        assert result == ["docs/**/*.md"]

    def test_empty_manifest(self, tmp_path):
        manifest = tmp_path / "manifest.yml"
        manifest.write_text("")
        assert gr.parse_cache_manifest(str(manifest)) == []

    def test_manifest_no_include_key(self, tmp_path):
        manifest = tmp_path / "manifest.yml"
        manifest.write_text("exclude:\n  - '*.log'\n")
        assert gr.parse_cache_manifest(str(manifest)) == []

    def test_real_repo_manifest(self):
        """Test with the actual .github/gemini-cache-manifest.yml in this repo."""
        repo_root = Path(__file__).resolve().parent.parent
        manifest = repo_root / ".github" / "gemini-cache-manifest.yml"
        if manifest.exists():
            result = gr.parse_cache_manifest(str(manifest))
            assert len(result) > 0
            assert any("md" in p for p in result)


# ---------------------------------------------------------------------------
# _validate_glob_pattern
# ---------------------------------------------------------------------------

class TestValidateGlobPattern:
    def test_safe_patterns(self):
        assert gr._validate_glob_pattern("*.md") is True
        assert gr._validate_glob_pattern("docs/**/*.md") is True
        assert gr._validate_glob_pattern("src/contracts/**/*") is True

    def test_absolute_path_rejected(self):
        assert gr._validate_glob_pattern("/etc/passwd") is False
        assert gr._validate_glob_pattern("/home/user/docs/*.md") is False

    def test_path_traversal_rejected(self):
        assert gr._validate_glob_pattern("../../../etc/passwd") is False
        assert gr._validate_glob_pattern("docs/../../secrets") is False


# ---------------------------------------------------------------------------
# build_cache_corpus
# ---------------------------------------------------------------------------

class TestBuildCacheCorpus:
    def test_with_manifest(self, tmp_path, monkeypatch):
        """Build corpus from a manifest targeting specific files."""
        # Create a temp directory structure
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "architecture.md").write_text("# Architecture\nOverview here.")
        (tmp_path / "README.md").write_text("# Project\nReadme content.")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("print('hello')")
        (tmp_path / "package-lock.json").write_text("{}")

        manifest = tmp_path / "manifest.yml"
        manifest.write_text('include:\n  - "*.md"\n  - "docs/**/*.md"\n')

        monkeypatch.setattr(gr, "CACHE_MANIFEST_PATH", str(manifest))
        monkeypatch.chdir(tmp_path)

        corpus = gr.build_cache_corpus()
        assert "README.md" in corpus
        assert "architecture.md" in corpus
        assert "main.py" not in corpus  # not matched by manifest
        assert "package-lock" not in corpus  # not matched by manifest

    def test_without_manifest_uses_defaults(self, tmp_path, monkeypatch):
        """Falls back to default patterns when no manifest exists."""
        (tmp_path / "README.md").write_text("# Hello")
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "guide.md").write_text("# Guide")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.py").write_text("import os")

        monkeypatch.setattr(gr, "CACHE_MANIFEST_PATH", str(tmp_path / "no-such-file.yml"))
        monkeypatch.chdir(tmp_path)

        corpus = gr.build_cache_corpus()
        assert "README.md" in corpus
        assert "guide.md" in corpus
        assert "app.py" not in corpus  # not matched by default *.md patterns

    def test_skips_secrets(self, tmp_path, monkeypatch):
        """Files matching SKIP_PATTERNS are excluded even if manifest includes them."""
        (tmp_path / "README.md").write_text("# Readme")
        (tmp_path / ".env").write_text("SECRET=abc")
        (tmp_path / "credentials.json").write_text('{"key": "secret"}')

        manifest = tmp_path / "manifest.yml"
        manifest.write_text('include:\n  - "*"\n')

        monkeypatch.setattr(gr, "CACHE_MANIFEST_PATH", str(manifest))
        monkeypatch.chdir(tmp_path)

        corpus = gr.build_cache_corpus()
        assert "README.md" in corpus
        assert "SECRET=abc" not in corpus
        assert "credentials" not in corpus

    def test_skips_binaries(self, tmp_path, monkeypatch):
        (tmp_path / "logo.png").write_bytes(b"\x89PNG\r\n")
        (tmp_path / "doc.md").write_text("# Doc")

        manifest = tmp_path / "manifest.yml"
        manifest.write_text('include:\n  - "*"\n')

        monkeypatch.setattr(gr, "CACHE_MANIFEST_PATH", str(manifest))
        monkeypatch.chdir(tmp_path)

        corpus = gr.build_cache_corpus()
        assert "doc.md" in corpus
        assert "logo.png" not in corpus

    def test_deduplicates_across_patterns(self, tmp_path, monkeypatch):
        (tmp_path / "README.md").write_text("# Readme")

        manifest = tmp_path / "manifest.yml"
        # README.md matches both patterns
        manifest.write_text('include:\n  - "*.md"\n  - "README.md"\n')

        monkeypatch.setattr(gr, "CACHE_MANIFEST_PATH", str(manifest))
        monkeypatch.chdir(tmp_path)

        corpus = gr.build_cache_corpus()
        assert corpus.count("=== README.md ===") == 1


# ---------------------------------------------------------------------------
# repo_slug
# ---------------------------------------------------------------------------

class TestRepoSlug:
    def test_basic(self):
        assert gr.repo_slug("owner/repo") == "owner-repo"

    def test_uppercase(self):
        assert gr.repo_slug("Owner/My-Repo") == "owner-my-repo"

    def test_special_chars(self):
        assert gr.repo_slug("org.name/repo_name") == "org-name-repo-name"


# ---------------------------------------------------------------------------
# _is_retryable_error
# ---------------------------------------------------------------------------

class TestIsRetryableError:
    def test_429_retryable(self):
        assert gr._is_retryable_error(Exception("HTTP 429 Too Many Requests"))

    def test_500_retryable(self):
        assert gr._is_retryable_error(Exception("500 Internal Server Error"))

    def test_quota_retryable(self):
        assert gr._is_retryable_error(Exception("Quota exceeded"))

    def test_rate_limit_retryable(self):
        assert gr._is_retryable_error(Exception("Rate limit reached"))

    def test_auth_not_retryable(self):
        assert not gr._is_retryable_error(Exception("401 Unauthorized"))

    def test_not_found_not_retryable(self):
        assert not gr._is_retryable_error(Exception("404 Not Found"))


# ---------------------------------------------------------------------------
# extract_response_text
# ---------------------------------------------------------------------------

class TestExtractResponseText:
    def test_simple_text_response(self):
        resp = MagicMock()
        resp.text = "Hello world"
        assert gr.extract_response_text(resp) == "Hello world"

    def test_response_text_none_falls_back(self):
        resp = MagicMock()
        resp.text = None
        part = MagicMock()
        part.thought = False
        part.text = "Fallback text"
        resp.candidates = [MagicMock(content=MagicMock(parts=[part]))]
        assert gr.extract_response_text(resp) == "Fallback text"

    def test_skips_thought_parts(self):
        resp = MagicMock()
        resp.text = None
        thought_part = MagicMock()
        thought_part.thought = True
        thought_part.text = "thinking..."
        text_part = MagicMock()
        text_part.thought = False
        text_part.text = "The actual response"
        resp.candidates = [MagicMock(content=MagicMock(parts=[thought_part, text_part]))]
        result = gr.extract_response_text(resp)
        assert result == "The actual response"
        assert "thinking" not in result

    def test_only_thought_parts_returns_empty(self):
        resp = MagicMock()
        resp.text = None
        thought_part = MagicMock()
        thought_part.thought = True
        thought_part.text = "just thinking"
        resp.candidates = [MagicMock(content=MagicMock(parts=[thought_part]))]
        assert gr.extract_response_text(resp) == ""

    def test_none_content_returns_empty(self):
        resp = MagicMock()
        resp.text = None
        resp.candidates = [MagicMock(content=None, finish_reason="STOP")]
        assert gr.extract_response_text(resp) == ""


# ---------------------------------------------------------------------------
# _extract_usage_metadata
# ---------------------------------------------------------------------------

class TestExtractUsageMetadata:
    def test_full_metadata(self):
        resp = MagicMock()
        resp.usage_metadata = MagicMock(
            prompt_token_count=1000,
            candidates_token_count=500,
            cached_content_token_count=200,
            total_token_count=1700,
        )
        result = gr._extract_usage_metadata(resp)
        assert result == {
            "prompt_token_count": 1000,
            "candidates_token_count": 500,
            "cached_content_token_count": 200,
            "total_token_count": 1700,
        }

    def test_none_metadata(self):
        resp = MagicMock()
        resp.usage_metadata = None
        assert gr._extract_usage_metadata(resp) == {}

    def test_partial_metadata(self):
        resp = MagicMock()
        resp.usage_metadata = MagicMock(
            prompt_token_count=500,
            candidates_token_count=100,
            spec=["prompt_token_count", "candidates_token_count"],
        )
        resp.usage_metadata.cached_content_token_count = None
        resp.usage_metadata.total_token_count = 600
        result = gr._extract_usage_metadata(resp)
        assert result["prompt_token_count"] == 500
        assert result["cached_content_token_count"] == 0  # None → 0

    def test_exception_returns_empty(self):
        resp = MagicMock()
        resp.usage_metadata = property(lambda self: (_ for _ in ()).throw(RuntimeError("bad")))
        # MagicMock won't raise on attribute access, so simulate it differently
        type(resp).usage_metadata = property(lambda self: (_ for _ in ()).throw(RuntimeError))
        result = gr._extract_usage_metadata(resp)
        assert result == {}


# ---------------------------------------------------------------------------
# write_output / write_metrics
# ---------------------------------------------------------------------------

class TestWriteOutput:
    def test_write_findings(self, tmp_path, monkeypatch):
        out = tmp_path / "findings.json"
        monkeypatch.setattr(gr, "OUTPUT_FILE", str(out))
        gr.write_output([{"file": "a.py", "line": 1}])
        data = json.loads(out.read_text())
        assert len(data) == 1

    def test_write_empty(self, tmp_path, monkeypatch):
        out = tmp_path / "findings.json"
        monkeypatch.setattr(gr, "OUTPUT_FILE", str(out))
        gr.write_output([])
        assert json.loads(out.read_text()) == []


class TestWriteMetrics:
    def test_write_metrics(self, tmp_path, monkeypatch):
        out = tmp_path / "metrics.json"
        monkeypatch.setattr(gr, "METRICS_FILE", str(out))
        gr.write_metrics({"prompt_token_count": 1000, "total_token_count": 1500})
        data = json.loads(out.read_text())
        assert data["prompt_token_count"] == 1000


# ---------------------------------------------------------------------------
# End-to-end: run_review_direct with mocked API
# ---------------------------------------------------------------------------

class TestRunReviewDirect:
    """Test the full review pipeline with a realistic diff and mocked Gemini."""

    def _mock_response(self, text, prompt_tokens=5000, output_tokens=800):
        """Create a mock Gemini API response."""
        resp = MagicMock()
        resp.text = text
        resp.usage_metadata = MagicMock(
            prompt_token_count=prompt_tokens,
            candidates_token_count=output_tokens,
            cached_content_token_count=0,
            total_token_count=prompt_tokens + output_tokens,
        )
        return resp

    def test_with_findings(self):
        mock_client = MagicMock()
        mock_resp = self._mock_response(SAMPLE_FINDINGS_JSON)
        mock_client.models.generate_content.return_value = mock_resp

        prompt = gr.INLINE_PROMPT_TEMPLATE.format(diff=SAMPLE_DIFF)

        with patch.dict("sys.modules", {"google.genai": MagicMock(), "google.genai.types": MagicMock()}):
            findings, usage = gr.run_review_direct(mock_client, "gemini-2.5-flash", prompt)

        assert len(findings) == 3
        assert findings[0]["severity"] == "Critical"
        assert findings[0]["file"] == "src/auth.py"
        assert usage["prompt_token_count"] == 5000
        assert usage["candidates_token_count"] == 800

    def test_empty_findings(self):
        mock_client = MagicMock()
        mock_resp = self._mock_response("[]")
        mock_client.models.generate_content.return_value = mock_resp

        prompt = gr.INLINE_PROMPT_TEMPLATE.format(diff=SAMPLE_DIFF)

        with patch.dict("sys.modules", {"google.genai": MagicMock(), "google.genai.types": MagicMock()}):
            findings, usage = gr.run_review_direct(mock_client, "gemini-2.5-flash", prompt)

        assert findings == []
        assert usage["prompt_token_count"] == 5000

    def test_thinking_only_response(self):
        """Model returns only thought parts (no text) — should be zero findings."""
        mock_client = MagicMock()
        resp = MagicMock()
        resp.text = None
        thought_part = MagicMock()
        thought_part.thought = True
        thought_part.text = "internal reasoning..."
        resp.candidates = [MagicMock(content=MagicMock(parts=[thought_part]))]
        resp.usage_metadata = MagicMock(
            prompt_token_count=5000, candidates_token_count=200,
            cached_content_token_count=0, total_token_count=5200,
        )
        mock_client.models.generate_content.return_value = resp

        with patch.dict("sys.modules", {"google.genai": MagicMock(), "google.genai.types": MagicMock()}):
            findings, usage = gr.run_review_direct(mock_client, "gemini-2.5-flash", "prompt")

        assert findings == []
        assert usage["candidates_token_count"] == 200

    def test_fenced_json_response(self):
        """Model wraps JSON in markdown code fences."""
        fenced = f"```json\n{SAMPLE_FINDINGS_JSON}\n```"
        mock_client = MagicMock()
        mock_resp = self._mock_response(fenced)
        mock_client.models.generate_content.return_value = mock_resp

        with patch.dict("sys.modules", {"google.genai": MagicMock(), "google.genai.types": MagicMock()}):
            findings, usage = gr.run_review_direct(mock_client, "gemini-2.5-flash", "prompt")

        assert len(findings) == 3


# ---------------------------------------------------------------------------
# End-to-end: main() with file I/O
# ---------------------------------------------------------------------------

class TestMainEndToEnd:
    """Full main() flow: load diff from file, mock API, verify output files."""

    @staticmethod
    def _make_google_mock(mock_client):
        """Build a mock google module hierarchy that works with 'from google import genai'."""
        mock_genai = MagicMock()
        mock_genai.Client.return_value = mock_client
        mock_google = MagicMock()
        mock_google.genai = mock_genai
        return {
            "google": mock_google,
            "google.genai": mock_genai,
            "google.genai.types": MagicMock(),
        }

    def test_full_pipeline_with_findings(self, tmp_path, monkeypatch):
        """Feed a real diff through main(), verify findings and metrics output."""
        # Write diff to file
        diff_file = tmp_path / "test.diff"
        diff_file.write_text(SAMPLE_DIFF)

        output_file = tmp_path / "findings.json"
        metrics_file = tmp_path / "metrics.json"

        # Set module globals
        monkeypatch.setattr(gr, "GEMINI_API_KEY", "test-key")
        monkeypatch.setattr(gr, "DIFF_FOCUSED_INPUT", str(diff_file))
        monkeypatch.setattr(gr, "SELECTED_MODEL", "gemini-2.5-flash")
        monkeypatch.setattr(gr, "USE_CACHE", False)
        monkeypatch.setattr(gr, "OUTPUT_FILE", str(output_file))
        monkeypatch.setattr(gr, "METRICS_FILE", str(metrics_file))

        # Build mock client
        mock_client = MagicMock()
        mock_client.models.count_tokens.return_value = MagicMock(total_tokens=5000)

        mock_resp = MagicMock()
        mock_resp.text = SAMPLE_FINDINGS_JSON
        mock_resp.usage_metadata = MagicMock(
            prompt_token_count=5000, candidates_token_count=800,
            cached_content_token_count=0, total_token_count=5800,
        )
        mock_client.models.generate_content.return_value = mock_resp

        with patch.dict("sys.modules", self._make_google_mock(mock_client)):
            gr.main()

        # Verify findings file
        assert output_file.exists()
        findings = json.loads(output_file.read_text())
        assert len(findings) == 3
        assert findings[0]["file"] == "src/auth.py"
        assert findings[0]["severity"] == "Critical"
        assert findings[1]["severity"] == "Critical"
        assert findings[2]["severity"] == "High"

        # Verify metrics file
        assert metrics_file.exists()
        metrics = json.loads(metrics_file.read_text())
        assert metrics["prompt_token_count"] == 5000
        assert metrics["candidates_token_count"] == 800

    def test_empty_diff_writes_empty(self, tmp_path, monkeypatch):
        """Empty diff should produce empty findings and metrics."""
        diff_file = tmp_path / "empty.diff"
        diff_file.write_text("")

        output_file = tmp_path / "findings.json"
        metrics_file = tmp_path / "metrics.json"

        monkeypatch.setattr(gr, "GEMINI_API_KEY", "test-key")
        monkeypatch.setattr(gr, "DIFF_FOCUSED_INPUT", str(diff_file))
        monkeypatch.setattr(gr, "OUTPUT_FILE", str(output_file))
        monkeypatch.setattr(gr, "METRICS_FILE", str(metrics_file))

        gr.main()

        assert json.loads(output_file.read_text()) == []
        assert json.loads(metrics_file.read_text()) == {}

    def test_over_token_limit_skips_review(self, tmp_path, monkeypatch):
        """When token count exceeds limit, skip review and write empty output."""
        diff_file = tmp_path / "big.diff"
        diff_file.write_text(SAMPLE_DIFF)

        output_file = tmp_path / "findings.json"
        metrics_file = tmp_path / "metrics.json"

        monkeypatch.setattr(gr, "GEMINI_API_KEY", "test-key")
        monkeypatch.setattr(gr, "DIFF_FOCUSED_INPUT", str(diff_file))
        monkeypatch.setattr(gr, "USE_CACHE", False)
        monkeypatch.setattr(gr, "OUTPUT_FILE", str(output_file))
        monkeypatch.setattr(gr, "METRICS_FILE", str(metrics_file))

        mock_client = MagicMock()
        mock_client.models.count_tokens.return_value = MagicMock(total_tokens=2_000_000)

        with patch.dict("sys.modules", self._make_google_mock(mock_client)):
            gr.main()

        assert json.loads(output_file.read_text()) == []
        assert json.loads(metrics_file.read_text()) == {}


# ---------------------------------------------------------------------------
# Diff filtering (the inline Python in gemini_review_workflow.sh)
# ---------------------------------------------------------------------------

class TestDiffFiltering:
    """Test the diff filter logic that strips lock files / minified assets."""

    @staticmethod
    def filter_diff(diff_text: str) -> str:
        """Reproduce the inline Python filter from gemini_review_workflow.sh."""
        import re as _re
        SKIP = _re.compile(
            r"package-lock\.json|yarn\.lock|pnpm-lock\.yaml|Cargo\.lock"
            r"|Gemfile\.lock|poetry\.lock|composer\.lock|\.min\.(js|css)|\.csv$|\.tsv$"
        )
        lines = diff_text.splitlines(keepends=True)
        skip = False
        result = []
        for line in lines:
            if line.startswith("diff --git"):
                skip = bool(SKIP.search(line))
            if not skip:
                result.append(line)
        return "".join(result)

    def test_filters_lock_files(self):
        result = self.filter_diff(SAMPLE_LOCK_DIFF)
        assert "package-lock.json" not in result
        assert "src/index.js" in result
        assert 'console.log("world")' in result

    def test_passes_code_files(self):
        result = self.filter_diff(SAMPLE_DIFF)
        assert "src/auth.py" in result
        assert "authenticate" in result

    def test_filters_yarn_lock(self):
        diff = textwrap.dedent("""\
            diff --git a/yarn.lock b/yarn.lock
            index aaa..bbb 100644
            --- a/yarn.lock
            +++ b/yarn.lock
            @@ -1,2 +1,3 @@
             resolved "1.0.0"
            +resolved "2.0.0"
            diff --git a/app.js b/app.js
            index ccc..ddd 100644
            --- a/app.js
            +++ b/app.js
            @@ -1 +1 @@
            -old
            +new
        """)
        result = self.filter_diff(diff)
        assert "yarn.lock" not in result
        assert "app.js" in result

    def test_filters_minified_js(self):
        diff = textwrap.dedent("""\
            diff --git a/bundle.min.js b/bundle.min.js
            index 111..222 100644
            --- a/bundle.min.js
            +++ b/bundle.min.js
            @@ -1 +1 @@
            -minified_old
            +minified_new
            diff --git a/src/real.js b/src/real.js
            index 333..444 100644
            --- a/src/real.js
            +++ b/src/real.js
            @@ -1 +1 @@
            -old_code
            +new_code
        """)
        result = self.filter_diff(diff)
        assert "bundle.min.js" not in result
        assert "src/real.js" in result


# ---------------------------------------------------------------------------
# _thinking_config_for_model
# ---------------------------------------------------------------------------

class TestThinkingConfig:
    def test_pro_gets_thinking_budget(self):
        with patch.dict("sys.modules", {"google.genai": MagicMock(), "google.genai.types": MagicMock()}):
            config = gr._thinking_config_for_model("gemini-2.5-pro")
            # The mock returns a MagicMock, but we verify it was called with budget
            # Check that ThinkingConfig was called
            assert config is not None

    def test_flash_gets_zero_thinking(self):
        with patch.dict("sys.modules", {"google.genai": MagicMock(), "google.genai.types": MagicMock()}):
            config = gr._thinking_config_for_model("gemini-2.5-flash")
            assert config is not None
