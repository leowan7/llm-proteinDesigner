"""A timed-out or crashed GPU subprocess still uploads the designs on disk.

Before this branch all three pipelines ended the run on a bare error --
boltzgen and rfantibody returned from the stage handler, pxdesign let the
RuntimeError reach its outer ``except Exception``. Designs already written
to the container's output directory were then rmtree'd with the work dir,
so a run that had produced usable designs delivered none.

Three of the four tests here fake the GPU stage so that it writes designs
AND then dies, then assert both halves: the designs are uploaded, and the
run still reports FAILED (post_webhook keys the status off ``error`` --
boltzgen :807, pxdesign :513, rfantibody :561). The fourth drives the same
boltzgen timeout with nothing on disk, and pins that it uploads nothing
and posts the bare error.

Faked: the GPU subprocess, the input sanitize/convert step, and the
quiver tooling that only a real RF2 output satisfies. The
collect/rank/upload code each test drives is the production code.

Its own file, not appended to a per-tool test, because one fallback
lands in three pipelines at once.
"""
from __future__ import annotations

import csv
import importlib.util
import json
import os
import subprocess
import sys
import types

_REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..")
)
_PDB_UTILS = os.path.join(_REPO_ROOT, "backend", "pdb_utils")


def _load(mod_name: str, tool: str):
    """Import ``docker/<tool>/run_pipeline.py`` under its own module name."""
    if _PDB_UTILS not in sys.path:
        sys.path.insert(0, _PDB_UTILS)
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    path = os.path.join(_REPO_ROOT, "docker", tool, "run_pipeline.py")
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


bg = _load("boltzgen_run_pipeline", "boltzgen")
px = _load("pxdesign_run_pipeline", "pxdesign")
ra = _load("rfantibody_run_pipeline", "rfantibody")

_N_DESIGNS = 2
_TIMEOUT_TEXT = "timed out"


class _Recorder:
    """Records the upload-URL exchange, every PUT, and every webhook."""

    def __init__(self):
        self.requested: list[str] = []
        self.uploaded: list[tuple[str, str]] = []
        self.webhooks: list[dict] = []

    def request_upload_urls(self, endpoint, token, filenames):
        self.requested.extend(filenames)
        return {f: f"https://storage.invalid/{f}" for f in filenames}

    def upload_output(self, url, file_path):
        self.uploaded.append((url.rsplit("/", 1)[-1], file_path))

    def send_heartbeat(self, *a, **kw):
        pass

    def post_webhook(self, webhook_url, job_id, pod_id, payload):
        self.webhooks.append(payload)

    @property
    def upload_names(self) -> list[str]:
        return [n for n, _ in self.uploaded]


def _wire_io(monkeypatch, mod, rec):
    for name, fn in (
        ("request_upload_urls", rec.request_upload_urls),
        ("upload_output", rec.upload_output),
        ("send_heartbeat", rec.send_heartbeat),
        ("post_webhook", rec.post_webhook),
    ):
        monkeypatch.setattr(mod, name, fn)


def _set_env(monkeypatch, payload: dict) -> None:
    monkeypatch.setenv("JOB_PAYLOAD", json.dumps(payload))
    monkeypatch.setenv("JOB_TOKEN", "test-token")
    monkeypatch.setenv("WEBHOOK_URL", "https://hub.invalid/webhooks")
    monkeypatch.setenv("JOB_ID", "job-1")


# ---------------------------------------------------------------------------
# BoltzGen
# ---------------------------------------------------------------------------

_BUDGET = 5


def _write_boltzgen_output(output_dir: str) -> None:
    """Write what BoltzGen leaves behind: ranked CIFs + the metrics CSV.

    Layout and column names taken from find_design_files (which reads
    ``final_ranked_designs/final_{budget}_designs``) and parse_metrics_csv
    (``file_name`` plus the ``designfolding-*`` score family).
    """
    metrics_dir = os.path.join(output_dir, "intermediate_designs_inverse_folded")
    ranked_dir = os.path.join(
        output_dir, "final_ranked_designs", f"final_{_BUDGET}_designs",
    )
    os.makedirs(metrics_dir, exist_ok=True)
    os.makedirs(ranked_dir, exist_ok=True)

    names = [f"rank{i + 1}_spec" for i in range(_N_DESIGNS)]
    for name in names:
        with open(os.path.join(ranked_dir, f"{name}.cif"), "w") as fh:
            fh.write(f"data_{name}\n")

    csv_path = os.path.join(metrics_dir, "aggregate_metrics_analyze.csv")
    with open(csv_path, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "file_name", "design_iptm",
            "designfolding-complex_plddt", "designfolding-bb_rmsd",
        ])
        for i, name in enumerate(names):
            writer.writerow([f"{name}.cif", 0.80 - 0.1 * i, 0.88, 1.2])


def _run_boltzgen(monkeypatch, rec, *, write_designs: bool):
    def _fake_run_command(cmd, timeout=None, cwd=None):
        output_dir = cmd[cmd.index("--output") + 1]
        if write_designs:
            _write_boltzgen_output(output_dir)
        raise subprocess.TimeoutExpired(cmd, timeout or 0)

    def _fake_ensure_cif(target_input, work_dir, target_chain="A"):
        cif = os.path.join(work_dir, "target.cif")
        with open(cif, "w") as fh:
            fh.write("data_target\n")
        return cif, {}

    def _fake_write_yaml_spec(yaml_spec, target_cif, work_dir):
        spec = os.path.join(work_dir, "spec.yaml")
        with open(spec, "w") as fh:
            fh.write("entities: []\n")
        return spec

    monkeypatch.syspath_prepend(_REPO_ROOT)  # /opt/contracts in the image
    monkeypatch.setattr(bg, "startup_check", lambda: {})
    monkeypatch.setattr(bg, "archive_work_dir", lambda *a, **kw: None)
    monkeypatch.setattr(
        bg, "download_input",
        lambda url, dest: open(dest, "w").write("ATOM  target\n"),
    )
    monkeypatch.setattr(bg, "ensure_cif", _fake_ensure_cif)
    monkeypatch.setattr(bg, "write_yaml_spec", _fake_write_yaml_spec)
    monkeypatch.setattr(bg, "run_command", _fake_run_command)
    _wire_io(monkeypatch, bg, rec)

    _set_env(monkeypatch, {
        "job_id": "job-1",
        "job_spec": {
            "target_chain": "A",
            "parameters": {
                "yaml_spec": {"entities": [{"protein": {"id": "B"}}]},
                "num_designs": _N_DESIGNS,
                "budget": _BUDGET,
            },
        },
        "input_presigned_url": "https://storage.invalid/target.pdb",
        "upload_urls_endpoint": "https://hub.invalid/upload",
    })

    bg.main()
    assert rec.webhooks, "main() posted no webhook"
    return rec.webhooks[-1]


def test_a_boltzgen_timeout_still_fails_but_uploads_its_designs(monkeypatch):
    rec = _Recorder()
    payload = _run_boltzgen(monkeypatch, rec, write_designs=True)

    # Every design on disk reached Storage, plus the metrics CSV.
    assert rec.upload_names == [
        "design_001.cif", "design_002.cif", "metrics.csv",
    ]
    # ...and the run is still a failure, flagged partial.
    assert _TIMEOUT_TEXT in payload["error"]
    assert payload["partial"] is True
    assert payload["candidate_count"] == _N_DESIGNS
    # The inline structures are dropped: the backend reads a webhook's
    # output only on a complete run (the internal_status gate in backend/webhooks/router.py::runpod_webhook).
    assert "candidates" not in payload


def test_a_boltzgen_timeout_with_no_designs_posts_the_bare_error(monkeypatch):
    """A timeout that saved nothing posts exactly today's payload.

    The fall-through must not change the zero-design case: this path keeps
    posting a lone ``error`` key, with no ``partial`` flag and no candidate
    count, so whatever reads a failed run's payload sees the same shape it
    sees today. The ``list(payload) == ["error"]`` assertion below pins it.
    """
    rec = _Recorder()
    payload = _run_boltzgen(monkeypatch, rec, write_designs=False)

    assert rec.upload_names == []
    assert list(payload) == ["error"]
    assert _TIMEOUT_TEXT in payload["error"]


# ---------------------------------------------------------------------------
# PXDesign
# ---------------------------------------------------------------------------

def _write_pxdesign_output(output_dir: str) -> None:
    """summary.csv plus the passing-AF2-IG-easy PDBs find_design_files indexes."""
    designs_dir = os.path.join(output_dir, "passing-AF2-IG-easy")
    os.makedirs(designs_dir, exist_ok=True)

    names = [f"design_{i}" for i in range(_N_DESIGNS)]
    for name in names:
        with open(os.path.join(designs_dir, f"{name}.pdb"), "w") as fh:
            fh.write(f"ATOM  {name}\n")

    with open(os.path.join(output_dir, "summary.csv"), "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["design_name", "af2_iptm", "af2_plddt", "unscaled_i_pae"])
        for i, name in enumerate(names):
            writer.writerow([name, 0.80 - 0.1 * i, 0.88, 8.0])


def test_a_pxdesign_timeout_still_fails_but_uploads_its_designs(monkeypatch):
    rec = _Recorder()

    def _fake_run_pxdesign(spec_path, output_dir, num_designs, **kw):
        _write_pxdesign_output(output_dir)
        # run_command converts the timeout to this RuntimeError (:462).
        raise RuntimeError(
            f"Command timed out after {kw.get('timeout', 5400)}s: killed"
        )

    def _fake_ensure_cif(target_input, work_dir, target_chain="A"):
        cif = os.path.join(work_dir, "target.cif")
        with open(cif, "w") as fh:
            fh.write("data_target\n")
        return cif, {}

    monkeypatch.setattr(px, "startup_check_legacy", lambda: {})
    monkeypatch.setattr(px, "ship_raw", lambda *a, **kw: None)
    monkeypatch.setattr(
        px, "download_input",
        lambda url, dest: open(dest, "w").write("ATOM  target\n"),
    )
    monkeypatch.setattr(px, "ensure_cif", _fake_ensure_cif)
    monkeypatch.setattr(px, "build_yaml_spec", lambda *a, **kw: {"entities": []})
    monkeypatch.setattr(px, "validate_input", lambda spec_path: None)
    monkeypatch.setattr(px, "run_pxdesign", _fake_run_pxdesign)
    _wire_io(monkeypatch, px, rec)
    _set_env(monkeypatch, {})

    px.run_webhook_tier({
        "job_id": "job-1",
        "job_spec": {
            "target_chain": "A",
            "parameters": {"num_designs": _N_DESIGNS},
        },
        "input_presigned_url": "https://storage.invalid/target.pdb",
        "upload_urls_endpoint": "https://hub.invalid/upload",
    })

    assert rec.webhooks, "run_webhook_tier posted no webhook"
    payload = rec.webhooks[-1]

    assert rec.upload_names == [
        "design_001.pdb", "design_002.pdb", "metrics.csv",
    ]
    assert _TIMEOUT_TEXT in payload["error"]
    assert payload["partial"] is True
    assert payload["candidate_count"] == _N_DESIGNS
    assert "candidates" not in payload


# ---------------------------------------------------------------------------
# RFantibody
# ---------------------------------------------------------------------------

def test_an_rfantibody_timeout_still_fails_but_uploads_its_designs(
    tmp_path, monkeypatch,
):
    rec = _Recorder()
    pdb_dir = tmp_path / "rf2_predictions"
    names = [f"design_{i}" for i in range(_N_DESIGNS)]

    def _fake_stage_rf2(sequences_qv, predictions_qv, **kw):
        """Write what RF2 had already predicted, then time out."""
        pdb_dir.mkdir(exist_ok=True)
        for name in names:
            (pdb_dir / f"{name}.pdb").write_text(f"ATOM  {name}\n")
        raise subprocess.TimeoutExpired(["rf2"], kw.get("recycles", 0))

    def _fake_extract_scores(predictions_qv, scores_tsv):
        """Stand-in for qvscorefile: columns from parse_scores_tsv."""
        with open(scores_tsv, "w", newline="") as fh:
            writer = csv.writer(fh, delimiter="\t")
            writer.writerow(["tag", "interaction_pae", "pae", "pred_lddt"])
            for i, name in enumerate(names):
                writer.writerow([name, 8.0 + i, 9.0, 0.88])

    def _fake_normalize(src, dest, target_chain=None):
        with open(src) as fh_in, open(dest, "w") as fh_out:
            fh_out.write(fh_in.read())
        return types.SimpleNamespace(
            chains_kept=["A"], chains_dropped=[],
            residues_kept_per_chain={}, residues_dropped_per_chain={},
            changes=[],
        )

    import pipeline_normalize

    framework = tmp_path / "vhh.pdb"
    framework.write_text("ATOM  framework\n")

    monkeypatch.setattr(
        pipeline_normalize, "normalize_for_rfantibody", _fake_normalize,
    )
    monkeypatch.syspath_prepend(_REPO_ROOT)  # /opt/contracts in the image
    monkeypatch.setitem(ra.FRAMEWORKS, "VHH", str(framework))
    monkeypatch.setattr(ra, "startup_check", lambda: {})
    monkeypatch.setattr(ra, "archive_raw_outputs", lambda *a, **kw: None)
    monkeypatch.setattr(
        ra, "download_input",
        lambda url, dest: open(dest, "w").write("ATOM  target\n"),
    )
    monkeypatch.setattr(
        ra, "preprocess_target_pdb",
        lambda src, dest, target_chain="A": (
            open(dest, "w").write("ATOM  target\n"), {}
        )[1],
    )
    monkeypatch.setattr(ra, "stage_rfdiffusion", lambda *a, **kw: None)
    monkeypatch.setattr(ra, "stage_proteinmpnn", lambda *a, **kw: None)
    monkeypatch.setattr(ra, "stage_rf2", _fake_stage_rf2)
    monkeypatch.setattr(ra, "extract_scores", _fake_extract_scores)
    monkeypatch.setattr(
        ra, "extract_pdbs",
        lambda predictions_qv, out_dir: sorted(
            str(p) for p in pdb_dir.glob("*.pdb")
        ),
    )
    _wire_io(monkeypatch, ra, rec)

    _set_env(monkeypatch, {
        "job_id": "job-1",
        "job_spec": {
            "target_chain": "A",
            "parameters": {"num_designs": _N_DESIGNS, "framework": "VHH"},
        },
        "input_presigned_url": "https://storage.invalid/target.pdb",
        "upload_urls_endpoint": "https://hub.invalid/upload",
    })

    ra.main()
    assert rec.webhooks, "main() posted no webhook"
    payload = rec.webhooks[-1]

    assert rec.upload_names == [
        "design_001.pdb", "design_002.pdb", "metrics.csv",
    ]
    assert payload["error"].startswith("RF2 validation failed:")
    assert _TIMEOUT_TEXT in payload["error"]
    assert payload["partial"] is True
    assert payload["candidate_count"] == _N_DESIGNS
    assert "candidates" not in payload
