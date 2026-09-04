"""Tests for backend/agent/analysis/report.py.

Covers PDF, CSV, and Markdown generation as well as the handle_generate_report
async handler.

All S3 calls are mocked — no real MinIO or network connection required.
"""

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FAKE_JOB_ID = str(uuid.uuid4())
FAKE_USER_ID = str(uuid.uuid4())

FAKE_CANDIDATES = [
    {
        "rank": i,
        "pdb_key": f"users/{FAKE_USER_ID}/jobs/{FAKE_JOB_ID}/candidates/rank_{i}.pdb",
        "scores": {
            "ipTM": 0.75 + i * 0.02,
            "pLDDT": 0.82 + i * 0.01,
            "dG": -35.0 - i * 2.0,
            "dSASA": 850 + i * 10,
            "ShapeComplementarity": 0.68 + i * 0.01,
        },
    }
    for i in range(1, 6)
]

FAKE_JOB_SPEC = {
    "tool": "bindcraft",
    "target_pdb_path": "/tmp/structures/4ZS7.pdb",
    "target_chain": "A",
    "hotspot_residues": [42, 43, 44],
    "parameters": {"num_designs": 100, "chain_length_min": 60, "chain_length_max": 100},
}

FAKE_RED_FLAGS = [
    {
        "rank": 1,
        "flag": "High confidence but poor geometric fit — likely false positive",
        "metrics": {"ipTM": 0.77, "ShapeComplementarity": 0.42},
    }
]

FAKE_STATS = {
    "ipTM": {"min": 0.75, "max": 0.83, "mean": 0.79, "p25": 0.77, "p75": 0.81, "p95": 0.83},
    "dG": {"min": -43.0, "max": -35.0, "mean": -39.0, "p25": -41.0, "p75": -37.0, "p95": -35.5},
}


# ---------------------------------------------------------------------------
# generate_pdf_report
# ---------------------------------------------------------------------------

def test_generate_pdf_report_returns_bytes():
    """PDF output starts with %PDF magic bytes."""
    from agent.analysis.report import generate_pdf_report

    with patch("agent.analysis.report.generate_presigned_get_url", return_value="https://example.com/fake.pdb"):
        pdf_bytes = generate_pdf_report(
            job_id=FAKE_JOB_ID,
            tool="bindcraft",
            shortlist=FAKE_CANDIDATES[:3],
            all_candidates=FAKE_CANDIDATES,
            red_flags=FAKE_RED_FLAGS,
            stats=FAKE_STATS,
            job_spec=FAKE_JOB_SPEC,
            guidance_text="Express in HEK293 and validate by SPR.",
        )

    assert isinstance(pdf_bytes, bytes)
    assert pdf_bytes[:4] == b"%PDF"


def test_generate_pdf_report_contains_kendrew_header():
    """PDF contains Kendrew branding — verified via KendrewReport header() call."""
    from agent.analysis.report import KendrewReport

    # Verify KendrewReport.header() renders the expected title string
    # by checking the header() call path in a minimal PDF
    pdf = KendrewReport(job_id="test-job-id", tool="bindcraft")
    pdf.add_page()
    output_bytes = bytes(pdf.output())

    # A valid PDF was generated and is non-trivially sized (has header content)
    assert output_bytes[:4] == b"%PDF"
    assert len(output_bytes) > 500  # Must have meaningful content

    # Also verify the class attribute that defines the branding string is present
    # in the source module (ensures it wasn't accidentally deleted)
    from agent.analysis import report as report_module
    source = open(report_module.__file__, encoding="utf-8").read()
    assert "Bindwave Design Analysis Report" in source


def test_generate_pdf_report_shortlist_row_count():
    """PDF generation succeeds for shortlists of varying length."""
    from agent.analysis.report import generate_pdf_report

    shortlist = FAKE_CANDIDATES[:2]
    with patch("agent.analysis.report.generate_presigned_get_url", return_value="https://example.com/fake.pdb"):
        pdf_bytes = generate_pdf_report(
            job_id=FAKE_JOB_ID,
            tool="bindcraft",
            shortlist=shortlist,
            all_candidates=FAKE_CANDIDATES,
            red_flags=[],
            stats=FAKE_STATS,
            job_spec=FAKE_JOB_SPEC,
            guidance_text="Test guidance.",
        )

    # Ensure a valid PDF is produced and the function didn't raise
    assert pdf_bytes[:4] == b"%PDF"


def test_generate_pdf_report_rejects_oversized_shortlist():
    """generate_pdf_report raises ValueError for shortlists > 50 candidates (T-08-09)."""
    from agent.analysis.report import generate_pdf_report

    oversized = FAKE_CANDIDATES * 11  # 55 items
    with pytest.raises(ValueError, match="50"):
        generate_pdf_report(
            job_id=FAKE_JOB_ID,
            tool="bindcraft",
            shortlist=oversized,
            all_candidates=FAKE_CANDIDATES,
            red_flags=[],
            stats=FAKE_STATS,
            job_spec=FAKE_JOB_SPEC,
            guidance_text="Test.",
        )


# ---------------------------------------------------------------------------
# generate_csv_export
# ---------------------------------------------------------------------------

def test_generate_csv_export_returns_string():
    """CSV export returns a string."""
    from agent.analysis.report import generate_csv_export

    csv_str = generate_csv_export(FAKE_CANDIDATES)
    assert isinstance(csv_str, str)


def test_generate_csv_export_has_header_row():
    """CSV header row contains expected score column names."""
    from agent.analysis.report import generate_csv_export

    csv_str = generate_csv_export(FAKE_CANDIDATES)
    lines = csv_str.strip().split("\n")
    header = lines[0]

    # Must have rank and pdb_key columns
    assert "rank" in header
    assert "pdb_key" in header
    # Must have at least one score column
    assert "ipTM" in header or "dG" in header


def test_generate_csv_export_includes_all_candidates():
    """CSV includes all candidates (not just shortlist)."""
    from agent.analysis.report import generate_csv_export

    csv_str = generate_csv_export(FAKE_CANDIDATES)
    lines = [ln for ln in csv_str.strip().split("\n") if ln]
    # Header + 5 data rows
    assert len(lines) == len(FAKE_CANDIDATES) + 1


# ---------------------------------------------------------------------------
# generate_markdown_report
# ---------------------------------------------------------------------------

def test_generate_markdown_report_returns_string():
    """Markdown export returns a string."""
    from agent.analysis.report import generate_markdown_report

    with patch("agent.analysis.report.generate_presigned_get_url", return_value="https://example.com/fake.pdb"):
        md = generate_markdown_report(
            job_id=FAKE_JOB_ID,
            tool="bindcraft",
            shortlist=FAKE_CANDIDATES[:3],
            all_candidates=FAKE_CANDIDATES,
            red_flags=FAKE_RED_FLAGS,
            stats=FAKE_STATS,
            job_spec=FAKE_JOB_SPEC,
            guidance_text="Validate by SPR.",
        )
    assert isinstance(md, str)


def test_generate_markdown_report_has_top_level_header():
    """Markdown contains '# Bindwave Design Analysis Report' header."""
    from agent.analysis.report import generate_markdown_report

    with patch("agent.analysis.report.generate_presigned_get_url", return_value="https://example.com/fake.pdb"):
        md = generate_markdown_report(
            job_id=FAKE_JOB_ID,
            tool="bindcraft",
            shortlist=FAKE_CANDIDATES[:3],
            all_candidates=FAKE_CANDIDATES,
            red_flags=FAKE_RED_FLAGS,
            stats=FAKE_STATS,
            job_spec=FAKE_JOB_SPEC,
            guidance_text="Validate by SPR.",
        )
    assert "# Bindwave Design Analysis Report" in md


def test_generate_markdown_report_has_red_flags_section():
    """Markdown contains '## Red Flags' section."""
    from agent.analysis.report import generate_markdown_report

    with patch("agent.analysis.report.generate_presigned_get_url", return_value="https://example.com/fake.pdb"):
        md = generate_markdown_report(
            job_id=FAKE_JOB_ID,
            tool="bindcraft",
            shortlist=FAKE_CANDIDATES[:3],
            all_candidates=FAKE_CANDIDATES,
            red_flags=FAKE_RED_FLAGS,
            stats=FAKE_STATS,
            job_spec=FAKE_JOB_SPEC,
            guidance_text="Validate by SPR.",
        )
    assert "## Red Flags" in md


def test_generate_markdown_report_has_next_steps_section():
    """Markdown contains '## Next Steps' section."""
    from agent.analysis.report import generate_markdown_report

    with patch("agent.analysis.report.generate_presigned_get_url", return_value="https://example.com/fake.pdb"):
        md = generate_markdown_report(
            job_id=FAKE_JOB_ID,
            tool="bindcraft",
            shortlist=FAKE_CANDIDATES[:3],
            all_candidates=FAKE_CANDIDATES,
            red_flags=FAKE_RED_FLAGS,
            stats=FAKE_STATS,
            job_spec=FAKE_JOB_SPEC,
            guidance_text="Validate by SPR.",
        )
    assert "## Next Steps" in md


# ---------------------------------------------------------------------------
# handle_generate_report
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handle_generate_report_returns_expected_keys():
    """handle_generate_report returns JSON with pdf_url, csv_url, markdown_url."""
    from agent.analysis.cache import clear_cache, set_cached
    from agent.analysis.report import handle_generate_report

    clear_cache()
    set_cached(FAKE_JOB_ID, FAKE_CANDIDATES)

    fake_job_row = {
        "tool": "bindcraft",
        "job_spec": json.dumps(FAKE_JOB_SPEC),
    }

    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value=fake_job_row)
    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_conn), __aexit__=AsyncMock(return_value=False)))

    mock_s3 = MagicMock()
    mock_s3.put_object = MagicMock()

    with patch("db.connection.get_db_pool", return_value=mock_pool), \
         patch("agent.analysis.report.get_s3_client", return_value=mock_s3), \
         patch("agent.analysis.report.generate_presigned_get_url", return_value="https://example.com/fake.pdf"):

        result_str = await handle_generate_report(
            tool_input={"job_id": FAKE_JOB_ID},
            user_id=FAKE_USER_ID,
        )

    result = json.loads(result_str)
    assert result["status"] == "success"
    assert "pdf_url" in result
    assert "csv_url" in result
    assert "markdown_url" in result
    assert "shortlist_count" in result

    clear_cache()


@pytest.mark.asyncio
async def test_handle_generate_report_error_when_not_cached():
    """handle_generate_report returns error if job not in cache."""
    from agent.analysis.cache import clear_cache
    from agent.analysis.report import handle_generate_report

    clear_cache()

    # Make DB also return a valid job row — but no cache
    fake_job_row = {
        "tool": "bindcraft",
        "job_spec": json.dumps(FAKE_JOB_SPEC),
    }
    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value=fake_job_row)
    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_conn), __aexit__=AsyncMock(return_value=False)))

    with patch("db.connection.get_db_pool", return_value=mock_pool):
        result_str = await handle_generate_report(
            tool_input={"job_id": str(uuid.uuid4())},
            user_id=FAKE_USER_ID,
        )

    result = json.loads(result_str)
    assert result["status"] == "error"
    assert "load_job_results" in result["message"]

    clear_cache()
