"""Each design RF2 finishes reaches Storage while RF2 is still running.

What these pin:

1. The keepalive beats every 60 s. tools-hub meters a live-charged run and
   runs its balance stop only on a heartbeat
   (tools-hub ``webhooks/modal.py::_handle_heartbeat``).
2. Only records followed by another ``QV_TAG`` are read mid-run; the last
   one may still be mid-write.
3. A design is uploaded once, under one name, whichever of the sweep or
   main()'s final loop gets to it, and its partial carries the same
   ``pdb_key`` both times (tools-hub dedupes partials on that exact string,
   ``webhooks/modal.py::_hb_merge_inputs``).
4. A design whose mid-run upload failed is retried by the next sweep and
   is not offered to the live page until its bytes landed.

Faked: the GPU subprocesses and the quiver tooling, as in
test_timeout_keeps_designs.py. The streamer, sweep and upload loop are the
production code.
"""
from __future__ import annotations

import csv
import importlib.util
import inspect
import json
import os
import sys
import types

_REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..")
)
_PDB_UTILS = os.path.join(_REPO_ROOT, "backend", "pdb_utils")


def _load():
    if _PDB_UTILS not in sys.path:
        sys.path.insert(0, _PDB_UTILS)
    if "rfantibody_run_pipeline" in sys.modules:
        return sys.modules["rfantibody_run_pipeline"]
    path = os.path.join(_REPO_ROOT, "docker", "rfantibody", "run_pipeline.py")
    spec = importlib.util.spec_from_file_location("rfantibody_run_pipeline", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["rfantibody_run_pipeline"] = mod
    spec.loader.exec_module(mod)
    return mod


ra = _load()


class _Recorder:
    def __init__(self, refuse: set[str] | None = None):
        self.refuse = refuse or set()
        self.uploaded: list[str] = []
        self.beats: list[dict] = []
        self.webhooks: list[dict] = []

    def request_upload_urls(self, endpoint, token, filenames):
        return {f: f"https://storage.invalid/{f}" for f in filenames}

    def upload_output(self, url, file_path):
        name = url.rsplit("/", 1)[-1]
        if name in self.refuse:
            raise RuntimeError("storage down")
        self.uploaded.append(name)

    def send_heartbeat(self, webhook_url, job_id, stage, done, total, **kw):
        self.beats.append({"stage": stage, "done": done, "total": total, **kw})

    def post_webhook(self, webhook_url, job_id, pod_id, payload):
        self.webhooks.append(payload)

    @property
    def offered_keys(self) -> list[str]:
        return [
            b["new_candidate"]["pdb_key"] for b in self.beats
            if b.get("new_candidate")
        ]


def _wire(monkeypatch, rec):
    for name in ("request_upload_urls", "upload_output", "send_heartbeat",
                 "post_webhook"):
        monkeypatch.setattr(ra, name, getattr(rec, name))


def _record(tag: str, ipae: float) -> str:
    return (
        f"QV_TAG {tag}\n"
        f"QV_SCORE {tag} interaction_pae={ipae}|pae=9.0|pred_lddt=0.88\n"
        f"ATOM      1  CA  GLY H   1       0.000   0.000   0.000\n"
    )


def _write_qv(path, tags):
    with open(path, "w") as fh:
        for i, tag in enumerate(tags):
            fh.write(_record(tag, 8.0 + i))


def test_keepalive_beats_every_60_seconds():
    sig = inspect.signature(ra.keepalive_heartbeat)
    assert sig.parameters["interval_s"].default == 60


def test_the_last_record_is_left_for_main(tmp_path):
    qv = tmp_path / "p.qv"
    _write_qv(qv, ["d0_best", "d1_best", "d2_best"])
    records = ra._complete_qv_records(str(qv))
    assert [t for t, _, _ in records] == ["d0_best", "d1_best"]
    assert records[0][1] == "interaction_pae=8.0|pae=9.0|pred_lddt=0.88"
    assert all(not ln.startswith("QV_") for _, _, body in records for ln in body)
    assert ra._complete_qv_records(str(tmp_path / "missing.qv")) == []


def test_sweep_ships_each_finished_design_once(tmp_path, monkeypatch):
    rec = _Recorder()
    _wire(monkeypatch, rec)
    qv = tmp_path / "p.qv"
    out = tmp_path / "top"
    s = ra._DesignStreamer(str(qv), str(out), "https://hub.invalid/upload",
                           "tok", "https://hub.invalid/wh", "job-1", 4)

    _write_qv(qv, ["d0_best", "d1_best"])
    assert s.sweep() == (ra._RF2_STAGE, 1)
    _write_qv(qv, ["d0_best", "d1_best", "d2_best"])
    assert s.sweep() == (ra._RF2_STAGE, 2)
    assert s.sweep() == (ra._RF2_STAGE, 2)

    assert rec.uploaded == ["d0_best.pdb", "d1_best.pdb"]
    assert rec.offered_keys == ["d0_best.pdb", "d1_best.pdb"]
    assert (out / "d0_best.pdb").read_text().startswith("ATOM")
    cand = rec.beats[0]["new_candidate"]
    assert cand["rank"] == 1
    assert cand["i_pae"] == 8.0
    assert cand["plddt"] == 88.0


def test_a_failed_upload_is_retried_and_not_offered(tmp_path, monkeypatch):
    rec = _Recorder(refuse={"d0_best.pdb"})
    _wire(monkeypatch, rec)
    qv = tmp_path / "p.qv"
    _write_qv(qv, ["d0_best", "d1_best"])
    s = ra._DesignStreamer(str(qv), str(tmp_path / "top"),
                           "https://hub.invalid/upload", "tok",
                           "https://hub.invalid/wh", "job-1", 2)

    assert s.sweep() == (ra._RF2_STAGE, 0)
    assert rec.beats[-1].get("new_candidate") is None
    rec.refuse.clear()
    assert s.sweep() == (ra._RF2_STAGE, 1)
    assert rec.uploaded == ["d0_best.pdb"]
    assert rec.offered_keys == ["d0_best.pdb"]


def test_main_does_not_reupload_or_reoffer_a_streamed_design(
    tmp_path, monkeypatch,
):
    rec = _Recorder()
    tags = ["d0_best", "d1_best", "d2_best"]
    streamers: list = []

    class _Captured(ra._DesignStreamer):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            streamers.append(self)

    def _fake_stage_rf2(sequences_qv, predictions_qv, **kw):
        _write_qv(predictions_qv, tags)
        streamers[0].sweep()  # one keepalive tick mid-RF2

    def _fake_extract_scores(predictions_qv, scores_tsv):
        with open(scores_tsv, "w", newline="") as fh:
            writer = csv.writer(fh, delimiter="\t")
            writer.writerow(["tag", "interaction_pae", "pae", "pred_lddt"])
            for i, tag in enumerate(tags):
                writer.writerow([tag, 8.0 + i, 9.0, 0.88])

    def _fake_extract_pdbs(predictions_qv, out_dir):
        os.makedirs(out_dir, exist_ok=True)
        paths = []
        for tag in tags:
            path = os.path.join(out_dir, f"{tag}.pdb")
            with open(path, "w") as fh:
                fh.write("ATOM\n")
            paths.append(path)
        return paths

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
    monkeypatch.setattr(ra, "_DesignStreamer", _Captured)
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
    monkeypatch.setattr(ra, "extract_pdbs", _fake_extract_pdbs)
    _wire(monkeypatch, rec)
    monkeypatch.setenv("JOB_PAYLOAD", json.dumps({
        "job_id": "job-1",
        "job_spec": {
            "target_chain": "A",
            "parameters": {"num_designs": 3, "framework": "VHH"},
        },
        "input_presigned_url": "https://storage.invalid/target.pdb",
        "upload_urls_endpoint": "https://hub.invalid/upload",
    }))
    monkeypatch.setenv("JOB_TOKEN", "test-token")
    monkeypatch.setenv("WEBHOOK_URL", "https://hub.invalid/webhooks")
    monkeypatch.setenv("JOB_ID", "job-1")

    ra.main()

    assert rec.uploaded == [
        "d0_best.pdb", "d1_best.pdb", "d2_best.pdb", "metrics.csv",
    ]
    assert rec.offered_keys == ["d0_best.pdb", "d1_best.pdb", "d2_best.pdb"]
    ranks = [b["new_candidate"]["rank"] for b in rec.beats
             if b.get("new_candidate")]
    assert ranks == [1, 2, 3]
    payload = rec.webhooks[-1]
    assert "error" not in payload
    assert [c["pdb_key"] for c in payload["candidates"]] == [
        "designs/d0_best.pdb", "designs/d1_best.pdb", "designs/d2_best.pdb",
    ]
