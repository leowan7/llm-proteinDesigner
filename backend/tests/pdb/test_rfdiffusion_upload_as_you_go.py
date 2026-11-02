"""Each design's PDB is uploaded once, mid-run, as its scores parse.

Two invariants, both load-bearing:

1. The upload happens inside ``stage_af2_validation``, not only in
   main()'s final loop. Every hub-side route that rebuilds a part-way run
   keeps a streamed design only when its bytes are in Storage, so a
   design that never left the container is GPU time the user paid for and
   no later join can recover. Which routes run that rebuild is a hub-side
   question, and the stop-at-zero-balance one (live charging) is
   PR leowan7/tools-hub#416, unmerged as of 2026-10-06.
2. No design is uploaded twice. A signed upload for a key that already
   exists is refused, and the refusal fails the request it rides in, so a
   final loop that re-uploads what the stage already shipped would turn a
   complete run into a broken one.

Its own file, like the sibling rfdiffusion tests, so in-flight branches
do not collide at one file's end on merge.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import types

_REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..")
)
_PDB_UTILS = os.path.join(_REPO_ROOT, "backend", "pdb_utils")
_RUN_PIPELINE = os.path.join(
    _REPO_ROOT, "docker", "rfdiffusion", "run_pipeline.py"
)


def _load_pipeline():
    if _PDB_UTILS not in sys.path:
        sys.path.insert(0, _PDB_UTILS)
    if "rfdiffusion_run_pipeline" in sys.modules:
        return sys.modules["rfdiffusion_run_pipeline"]
    spec = importlib.util.spec_from_file_location(
        "rfdiffusion_run_pipeline", _RUN_PIPELINE
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["rfdiffusion_run_pipeline"] = mod
    spec.loader.exec_module(mod)
    return mod


rfd = _load_pipeline()

_N_DESIGNS = 2


def _write_af2_output(out_dir: str, design_name: str, iptm: float) -> str:
    """Write what ColabFold leaves behind for one design.

    Shape taken from parse_af2_scores: ``{design}*scores*.json`` carrying
    iptm/plddt/pae, plus a rank_001 model PDB.
    """
    os.makedirs(out_dir, exist_ok=True)
    scores = os.path.join(
        out_dir, f"{design_name}_scores_rank_001_model_1_seed_000.json"
    )
    n = 20
    with open(scores, "w") as fh:
        json.dump(
            {
                "iptm": iptm,
                "plddt": [80.0] * n,
                "pae": [[5.0] * n for _ in range(n)],
            },
            fh,
        )
    complex_pdb = os.path.join(
        out_dir, f"{design_name}_unrelaxed_rank_001_model_1.pdb"
    )
    with open(complex_pdb, "w") as fh:
        fh.write(f"ATOM  {design_name}\n")
    return complex_pdb


class _Recorder:
    """Records every upload-URL request and every PUT."""

    def __init__(self, fail_first: set[str] | None = None):
        self.requested: list[str] = []
        self.uploaded: list[tuple[str, str]] = []
        self.heartbeats: list[dict] = []
        self.webhooks: list[dict] = []
        self._fail_first = set(fail_first or ())

    def request_upload_urls(self, endpoint, token, filenames):
        self.requested.extend(filenames)
        return {f: f"https://storage.invalid/{f}" for f in filenames}

    def upload_output(self, url, file_path):
        name = url.rsplit("/", 1)[-1]
        if name in self._fail_first:
            self._fail_first.discard(name)
            raise RuntimeError(f"simulated upload failure for {name}")
        self.uploaded.append((name, file_path))

    def send_heartbeat(self, webhook_url, job_id, stage, *a, **kw):
        self.heartbeats.append(
            {"stage": stage, "new_candidate": kw.get("new_candidate")}
        )

    def post_webhook(self, webhook_url, job_id, pod_id, payload):
        self.webhooks.append(payload)

    @property
    def upload_names(self) -> list[str]:
        return [n for n, _ in self.uploaded]


def _patch_stage(monkeypatch, rec, tmp_path, rfdiff_dir):
    """Fakes shared by both entry points: AF2 subprocess, uploads, HB."""

    def _fake_run(cmd, **kwargs):
        # _build_af2_cmd: argv[1] is the FASTA, argv[2] the output dir.
        out_dir = cmd[2]
        _write_af2_output(out_dir, os.path.basename(out_dir), iptm=0.5)
        return ""

    real_path = rfd.Path

    def _path(p):
        q = real_path(p)
        return tmp_path / "jax" if q.as_posix() == "/root/.cache/jax" else q

    monkeypatch.setattr(rfd, "Path", _path)
    monkeypatch.setattr(rfd, "run_command", _fake_run)
    monkeypatch.setattr(
        rfd, "_extract_target_sequence", lambda pdb, chain: "MTARGETSEQ"
    )
    monkeypatch.setattr(rfd, "request_upload_urls", rec.request_upload_urls)
    monkeypatch.setattr(rfd, "upload_output", rec.upload_output)
    monkeypatch.setattr(rfd, "send_heartbeat", rec.send_heartbeat)
    os.makedirs(rfdiff_dir, exist_ok=True)


def _fastas(tmp_path) -> list[str]:
    out = []
    mpnn = tmp_path / "mpnn_output"
    mpnn.mkdir(exist_ok=True)
    for i in range(_N_DESIGNS):
        f = mpnn / f"design_{i}.fa"
        f.write_text(f">backbone\nGGGGGGGGGG\n>design_{i}\n{'W' * (10 + i)}\n")
        out.append(str(f))
    return out


# ---------------------------------------------------------------------------
# 1. The stage itself uploads, and tells the heartbeat where it landed.
# ---------------------------------------------------------------------------

def test_stage_uploads_each_design_as_its_scores_parse(tmp_path, monkeypatch):
    rec = _Recorder()
    rfdiff_dir = tmp_path / "rfdiffusion_output"
    _patch_stage(monkeypatch, rec, tmp_path, rfdiff_dir)

    results = rfd.stage_af2_validation(
        _fastas(tmp_path), "target.pdb", "A", str(tmp_path / "af2"),
        webhook_url="https://hub.invalid/webhooks", job_id="job-1",
        upload_endpoint="https://hub.invalid/upload", job_token="tok",
        rfdiff_output=str(rfdiff_dir),
    )

    assert len(results) == _N_DESIGNS
    assert rec.upload_names == ["design_0.pdb", "design_1.pdb"]
    # The object uploaded is the AF2 complex the scores were read from,
    # not the RFdiffusion backbone.
    for name, path in rec.uploaded:
        assert "rank_001" in os.path.basename(path), path

    keys = [r["pdb_key"] for r in results]
    assert keys == ["designs/design_0.pdb", "designs/design_1.pdb"]

    # ...and the heartbeat carries that same key, which is the half
    # tools-hub's shared/job_recovery.py::reconstruct reads.
    hb_keys = [
        hb["new_candidate"]["pdb_key"]
        for hb in rec.heartbeats
        if hb["new_candidate"]
    ]
    assert hb_keys == keys


def test_stage_without_an_upload_endpoint_uploads_nothing(tmp_path, monkeypatch):
    """The smoke/mini_pilot path returns structures inline and has no
    endpoint; it must not start requesting URLs."""
    rec = _Recorder()
    rfdiff_dir = tmp_path / "rfdiffusion_output"
    _patch_stage(monkeypatch, rec, tmp_path, rfdiff_dir)

    results = rfd.stage_af2_validation(
        _fastas(tmp_path), "target.pdb", "A", str(tmp_path / "af2"),
        rfdiff_output=str(rfdiff_dir),
    )

    assert rec.requested == []
    assert rec.upload_names == []
    assert [r["pdb_key"] for r in results] == [None] * _N_DESIGNS


# ---------------------------------------------------------------------------
# 2. Across a whole run, each design is uploaded exactly once.
# ---------------------------------------------------------------------------

def _run_main(tmp_path, monkeypatch, rec):
    """Drive main()'s production path with only the GPU stages faked.

    stage_af2_validation and the final upload loop are the real ones --
    the double-upload this file guards against can only be seen with both
    running in one pass.
    """
    rfdiff_dir_holder: dict = {}

    def _fake_rfdiffusion(target_pdb, job_spec, out_dir, **kw):
        rfdiff_dir_holder["dir"] = out_dir
        pdbs = []
        for i in range(_N_DESIGNS):
            p = os.path.join(out_dir, f"design_{i}.pdb")
            with open(p, "w") as fh:
                fh.write("ATOM  backbone\n")
            pdbs.append(p)
        return pdbs

    def _fake_mpnn(backbone_pdbs, target_chain, out_dir, **kw):
        os.makedirs(out_dir, exist_ok=True)
        out = []
        for i in range(_N_DESIGNS):
            p = os.path.join(out_dir, f"design_{i}.fa")
            with open(p, "w") as fh:
                fh.write(f">backbone\nGGGGGGGGGG\n>design_{i}\n{'W' * (10 + i)}\n")
            out.append(p)
        return out

    def _fake_run(cmd, **kwargs):
        out_dir = cmd[2]
        _write_af2_output(out_dir, os.path.basename(out_dir), iptm=0.5)
        return ""

    real_path = rfd.Path

    def _path(p):
        q = real_path(p)
        return tmp_path / "jax" if q.as_posix() == "/root/.cache/jax" else q

    def _normalize_for_rfdiffusion(src, dest, target_chain=None):
        with open(src) as fh_in, open(dest, "w") as fh_out:
            fh_out.write(fh_in.read())
        return types.SimpleNamespace(
            chains_requested=["A"], chains_kept=["A"], chains_dropped=[],
            residues_kept_per_chain={}, residues_dropped_per_chain={},
            changes=[],
        )

    # Patch the attribute on the real module, not the module itself: it
    # also exports parse_target_chains, which rfd imports from it.
    import pipeline_normalize

    monkeypatch.setattr(
        pipeline_normalize, "normalize_for_rfdiffusion",
        _normalize_for_rfdiffusion,
    )
    monkeypatch.syspath_prepend(_REPO_ROOT)  # /opt/contracts in the image
    monkeypatch.setattr(rfd, "startup_check", lambda: {})
    monkeypatch.setattr(rfd, "download_af2_weights", lambda: None)
    monkeypatch.setattr(rfd, "ship_raw_archive", lambda *a, **kw: None)
    monkeypatch.setattr(
        rfd, "download_input",
        lambda url, dest: open(dest, "w").write("ATOM  target\n"),
    )
    monkeypatch.setattr(rfd, "stage_rfdiffusion", _fake_rfdiffusion)
    monkeypatch.setattr(rfd, "stage_proteinmpnn", _fake_mpnn)
    monkeypatch.setattr(rfd, "Path", _path)
    monkeypatch.setattr(rfd, "run_command", _fake_run)
    monkeypatch.setattr(
        rfd, "_extract_target_sequence", lambda pdb, chain: "MTARGETSEQ"
    )
    monkeypatch.setattr(rfd, "request_upload_urls", rec.request_upload_urls)
    monkeypatch.setattr(rfd, "upload_output", rec.upload_output)
    monkeypatch.setattr(rfd, "send_heartbeat", rec.send_heartbeat)
    monkeypatch.setattr(rfd, "post_webhook", rec.post_webhook)

    monkeypatch.setenv("JOB_PAYLOAD", json.dumps({
        "job_id": "job-1",
        "job_spec": {"target_chain": "A", "parameters": {}},
        "input_presigned_url": "https://storage.invalid/target.pdb",
        "upload_urls_endpoint": "https://hub.invalid/upload",
    }))
    monkeypatch.setenv("JOB_TOKEN", "test-token")
    monkeypatch.setenv("WEBHOOK_URL", "https://hub.invalid/webhooks")
    monkeypatch.setenv("JOB_ID", "job-1")

    rfd.main()
    assert rec.webhooks, "main() posted no webhook"
    return rec.webhooks[-1]


def test_no_design_is_uploaded_twice_in_a_whole_run(tmp_path, monkeypatch):
    rec = _Recorder()
    payload = _run_main(tmp_path, monkeypatch, rec)

    design_requests = [n for n in rec.requested if n.endswith(".pdb")]
    assert sorted(design_requests) == ["design_0.pdb", "design_1.pdb"]
    assert sorted(n for n in rec.upload_names if n.endswith(".pdb")) == [
        "design_0.pdb", "design_1.pdb",
    ]
    assert rec.upload_names.count("metrics.csv") == 1
    assert "failed_uploads" not in payload

    # The final payload points at the keys the stage uploaded under.
    assert sorted(c["pdb_key"] for c in payload["candidates"]) == [
        "designs/design_0.pdb", "designs/design_1.pdb",
    ]
    hb_keys = {
        hb["new_candidate"]["pdb_key"]
        for hb in rec.heartbeats
        if hb["new_candidate"]
    }
    assert hb_keys == {c["pdb_key"] for c in payload["candidates"]}


def test_a_failed_mid_run_upload_is_retried_and_does_not_fail_the_run(
    tmp_path, monkeypatch,
):
    """The mid-run PUT for design_1 fails once. The run keeps going, the
    final loop retries that design and only that design, and the result
    is a clean success."""
    rec = _Recorder(fail_first={"design_1.pdb"})
    payload = _run_main(tmp_path, monkeypatch, rec)

    assert rec.requested.count("design_0.pdb") == 1
    assert rec.requested.count("design_1.pdb") == 2  # mid-run + retry
    assert sorted(n for n in rec.upload_names if n.endswith(".pdb")) == [
        "design_0.pdb", "design_1.pdb",
    ]
    assert "failed_uploads" not in payload
    assert len(payload["candidates"]) == _N_DESIGNS


def test_an_unrecoverable_upload_is_reported_as_failed(tmp_path, monkeypatch):
    """Both attempts fail for design_1: the run still completes, and
    failed_uploads names it so tools-hub keeps the inline structure
    instead of dropping it as already-in-Storage."""
    rec = _Recorder(fail_first={"design_1.pdb"})
    # fail_first discards after one failure; make every attempt fail.
    real_upload = rec.upload_output

    def _always_fail_design_1(url, file_path):
        if url.endswith("design_1.pdb"):
            raise RuntimeError("simulated permanent failure")
        return real_upload(url, file_path)

    rec.upload_output = _always_fail_design_1
    payload = _run_main(tmp_path, monkeypatch, rec)

    assert payload["failed_uploads"] == ["design_1.pdb"]
    assert len(payload["candidates"]) == _N_DESIGNS
