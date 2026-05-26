"""Tests for result download as ZIP archive (RESULT-01, RESULT-02).

Covers:
- Download endpoint returns a ZIP file with correct content-type and filename
- ZIP contains PDB files
- ZIP contains a summary report text file

Implementation target: Plan 03-03.
"""

import io
import zipfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from auth.dependencies import get_current_user
from main import app


def _override_user(user_id: str = "u1"):
    """Return a FastAPI dependency override that yields a fixed user ID."""
    async def _dep():
        return user_id
    return _dep


class TestResultDownload:
    """RESULT-01 / RESULT-02: Results are downloadable as a ranked ZIP archive."""

    def _make_s3_mock(self, keys_and_content):
        """Build an S3 client mock for list_objects_v2 + get_object."""
        contents = [{"Key": k} for k, _ in keys_and_content]
        mock_s3 = MagicMock()
        mock_s3.list_objects_v2 = MagicMock(return_value={"Contents": contents})
        content_by_key = {k: v for k, v in keys_and_content}

        def fake_get_object(Bucket, Key):
            return {"Body": io.BytesIO(content_by_key[Key])}

        mock_s3.get_object = MagicMock(side_effect=fake_get_object)
        return mock_s3

    def _make_pool(self, status="complete"):
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value={"status": status})
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        ctx.__aexit__ = AsyncMock(return_value=False)
        mock_pool = AsyncMock()
        mock_pool.acquire = MagicMock(return_value=ctx)
        return mock_pool

    @pytest.mark.anyio
    async def test_download_returns_zip(self):
        """Mock S3 list_objects_v2 and get_object. Verify:
        - Response Content-Type is 'application/zip'
        - Content-Disposition filename is 'job_{id}_designs.zip'
        """
        s3_mock = self._make_s3_mock([
            ("users/u1/jobs/job-dl/outputs/design_001.pdb", b"ATOM   1  CA  ALA"),
            ("users/u1/jobs/job-dl/outputs/report.txt", b"Summary report"),
        ])
        pool_mock = self._make_pool()

        app.dependency_overrides[get_current_user] = _override_user("u1")
        try:
            with (
                patch("jobs.router.get_db_pool", return_value=pool_mock),
                patch("jobs.router.get_s3_client", return_value=s3_mock),
            ):
                from httpx import AsyncClient, ASGITransport
                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    response = await client.get(
                        "/jobs/job-dl/download",
                        cookies={"access_token": "fake-token"},
                    )
        finally:
            app.dependency_overrides.pop(get_current_user, None)

        assert response.status_code == 200
        assert "application/zip" in response.headers["content-type"]
        assert "job-dl" in response.headers["content-disposition"]

    @pytest.mark.anyio
    async def test_zip_contains_ranked_pdbs(self):
        """Verify the returned ZIP archive contains PDB files."""
        pdb_content = b"ATOM   1  CA  ALA A   1      1.0  1.0  1.0  1.0  0.0           C"
        s3_mock = self._make_s3_mock([
            ("users/u1/jobs/job-pdb/outputs/design_001.pdb", pdb_content),
            ("users/u1/jobs/job-pdb/outputs/design_002.pdb", pdb_content),
            ("users/u1/jobs/job-pdb/outputs/report.txt", b"Report"),
        ])
        pool_mock = self._make_pool()

        app.dependency_overrides[get_current_user] = _override_user("u1")
        try:
            with (
                patch("jobs.router.get_db_pool", return_value=pool_mock),
                patch("jobs.router.get_s3_client", return_value=s3_mock),
            ):
                from httpx import AsyncClient, ASGITransport
                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    response = await client.get(
                        "/jobs/job-pdb/download",
                        cookies={"access_token": "fake-token"},
                    )
        finally:
            app.dependency_overrides.pop(get_current_user, None)

        assert response.status_code == 200
        zf = zipfile.ZipFile(io.BytesIO(response.content))
        names = zf.namelist()
        pdb_files = [n for n in names if n.endswith(".pdb")]
        assert len(pdb_files) >= 1

    @pytest.mark.anyio
    async def test_report_in_zip(self):
        """Verify the ZIP archive contains a text summary report file."""
        s3_mock = self._make_s3_mock([
            ("users/u1/jobs/job-rpt/outputs/design_001.pdb", b"ATOM data"),
            ("users/u1/jobs/job-rpt/outputs/report.txt", b"Job summary\nnext_steps: check scores"),
        ])
        pool_mock = self._make_pool()

        app.dependency_overrides[get_current_user] = _override_user("u1")
        try:
            with (
                patch("jobs.router.get_db_pool", return_value=pool_mock),
                patch("jobs.router.get_s3_client", return_value=s3_mock),
            ):
                from httpx import AsyncClient, ASGITransport
                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    response = await client.get(
                        "/jobs/job-rpt/download",
                        cookies={"access_token": "fake-token"},
                    )
        finally:
            app.dependency_overrides.pop(get_current_user, None)

        assert response.status_code == 200
        zf = zipfile.ZipFile(io.BytesIO(response.content))
        names = zf.namelist()
        report_files = [n for n in names if "report" in n or n.endswith(".txt")]
        assert len(report_files) >= 1
