"""Job.download_results / download_results_async tests (Phase 13, Plan 13-05).

respx mocks each candidate's presigned download_url; files are written under a
pytest tmp_path. No real network, no real filesystem outside tmp_path.
"""

import httpx
import respx

from bindwave.types.job import Job

_DL_1 = "https://r2.bindwave.test/presigned/cand-1"
_DL_2 = "https://r2.bindwave.test/presigned/cand-2"


def _job() -> Job:
    return Job.model_validate(
        {
            "id": "j-1",
            "tool": "rfdiffusion",
            "status": "complete",
            "created_at": "2026-06-05T12:00:00+00:00",
            "candidates": [
                {"rank": 1, "pdb_key": "k1", "download_url": _DL_1, "scores": {}},
                {"rank": 2, "pdb_key": "k2", "download_url": _DL_2, "scores": {}},
            ],
        }
    )


@respx.mock
def test_download_results_writes_files(tmp_path):
    respx.get(_DL_1).mock(return_value=httpx.Response(200, content=b"PDB CONTENT 1"))
    respx.get(_DL_2).mock(return_value=httpx.Response(200, content=b"PDB CONTENT 2"))
    job = _job()
    result = job.download_results(dest_dir=tmp_path)
    assert set(result.keys()) == {1, 2}
    assert result[1] == tmp_path / "j-1-candidate-1.pdb"
    assert result[2] == tmp_path / "j-1-candidate-2.pdb"
    assert result[1].read_bytes() == b"PDB CONTENT 1"
    assert result[2].read_bytes() == b"PDB CONTENT 2"


@respx.mock
def test_download_results_creates_dest_dir(tmp_path):
    respx.get(_DL_1).mock(return_value=httpx.Response(200, content=b"PDB"))
    respx.get(_DL_2).mock(return_value=httpx.Response(200, content=b"PDB"))
    new_dir = tmp_path / "results" / "run1"
    assert not new_dir.exists()
    job = _job()
    result = job.download_results(dest_dir=new_dir)
    assert new_dir.is_dir()
    assert all(p.is_file() for p in result.values())


@respx.mock
async def test_download_results_async(tmp_path):
    respx.get(_DL_1).mock(return_value=httpx.Response(200, content=b"ASYNC PDB 1"))
    respx.get(_DL_2).mock(return_value=httpx.Response(200, content=b"ASYNC PDB 2"))
    job = _job()
    result = await job.download_results_async(dest_dir=tmp_path)
    assert set(result.keys()) == {1, 2}
    assert result[1].read_bytes() == b"ASYNC PDB 1"
    assert result[2].read_bytes() == b"ASYNC PDB 2"
