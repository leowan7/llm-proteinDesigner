"""Tests for result download as ZIP archive (RESULT-01, RESULT-02).

Covers:
- Download endpoint returns a ZIP file with correct content-type and filename
- ZIP contains PDB files named by rank (rank_01.pdb, rank_02.pdb, etc.)
- ZIP contains a summary report text file

Implementation target: Plan 03-03.
"""

import pytest


class TestResultDownload:
    """RESULT-01 / RESULT-02: Results are downloadable as a ranked ZIP archive."""

    def test_download_returns_zip(self):
        """Mock S3 list_objects_v2 and get_object. Verify:
        - Response Content-Type is 'application/zip'
        - Content-Disposition filename is 'job_{id}_designs.zip'

        Stub — implementation in Plan 03-03.
        """
        pytest.skip("STUB -- implementation in Plan 03-03")

    def test_zip_contains_ranked_pdbs(self):
        """Verify the returned ZIP archive contains PDB files named by rank
        (e.g. rank_01.pdb, rank_02.pdb) corresponding to the ordered candidates
        in job_candidates table.

        Stub — implementation in Plan 03-03.
        """
        pytest.skip("STUB -- implementation in Plan 03-03")

    def test_report_in_zip(self):
        """Verify the ZIP archive contains a text summary report file
        (e.g. report.txt or summary.txt) with human-readable job metadata,
        design scores, and next_steps guidance.

        Stub — implementation in Plan 03-03.
        """
        pytest.skip("STUB -- implementation in Plan 03-03")
