"""Each finished PXDesign design reaches Storage while the run is still going.

Three invariants:

1. Designs are uploaded from the heartbeat tick, not only from the upload
   loop that runs after PXDesign exits. A pilot run is budgeted up to 90
   minutes (``run_pxdesign(..., timeout=5400)``); a run cancelled, killed or
   stopped for balance before that loop delivers nothing it had already paid
   for, because the hub rebuilds a stopped run only from bytes already in
   Storage (tools-hub ``shared/job_recovery.py::reconstruct``).
2. No design is uploaded twice, and the streamed and final names for one
   design are the same string. One failed name fails the whole signing
   request (tools-hub ``webhooks/uploads.py::_handle``) and the signing call
   passes no upsert (tools-hub ``shared/storage.py::presigned_output_put_url``;
   read from that code, not observed refusing a live bucket), so each file is
   signed on its own. The hub dedupes partials on the exact ``pdb_key``
   (tools-hub ``webhooks/modal.py::_hb_merge_inputs``), so two spellings of
   one design would list it twice.
3. A design is only offered to the live page once its bytes landed, and only
   once it looks fully written. PXDesign's own write path is not readable
   from this repo, so completeness is the heuristic in ``_looks_complete``;
   these tests pin that heuristic's behaviour, not upstream's write order.

Its own file, like the sibling tool tests in this directory, so in-flight
branches do not collide at one file's end on merge.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import time

import pytest

gemmi = pytest.importorskip("gemmi")

_REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..")
)
_PDB_UTILS = os.path.join(_REPO_ROOT, "backend", "pdb_utils")
_RUN_PIPELINE = os.path.join(
    _REPO_ROOT, "docker", "pxdesign", "run_pipeline.py"
)

yaml = pytest.importorskip("yaml")


def _load_pipeline():
    if _PDB_UTILS not in sys.path:
        sys.path.insert(0, _PDB_UTILS)
    if "pxdesign_run_pipeline" in sys.modules:
        return sys.modules["pxdesign_run_pipeline"]
    spec = importlib.util.spec_from_file_location(
        "pxdesign_run_pipeline", _RUN_PIPELINE
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["pxdesign_run_pipeline"] = mod
    spec.loader.exec_module(mod)
    return mod


pxd = _load_pipeline()


def _write_design(output_dir, stem, seed="seed_000", end=True, quiesced=True):
    """Write one design where the sweep looks for it.

    The observed layout, from find_design_files' docstring:
    ``<output_dir>/global_run_0/<spec>/seed_<NNN>/predictions/converted_pdbs/``.
    ``end``/``quiesced`` turn off one completeness signal at a time.
    """
    design_dir = os.path.join(
        output_dir, "global_run_0", "spec", seed, "predictions",
        "converted_pdbs",
    )
    os.makedirs(design_dir, exist_ok=True)
    path = os.path.join(design_dir, f"{stem}.pdb")
    with open(path, "w") as fh:
        fh.write("ATOM      1  CA  ALA A   1       0.000   0.000   0.000\n")
        if end:
            fh.write("END\n")
    if quiesced:
        old = time.time() - (pxd._QUIESCE_SECONDS + 5)
        os.utime(path, (old, old))
    return path


class _Recorder:
    """Records every signing request, every PUT and every heartbeat."""

    def __init__(self, fail_first: set[str] | None = None):
        self.requested: list[str] = []
        self.uploaded: list[tuple[str, str]] = []
        self.heartbeats: list[dict] = []
        self._fail_first = set(fail_first or ())

    def install(self, monkeypatch) -> None:
        monkeypatch.setattr(pxd, "request_upload_urls", self.request_upload_urls)
        monkeypatch.setattr(pxd, "upload_output", self.upload_output)
        monkeypatch.setattr(pxd, "send_heartbeat", self.send_heartbeat)

    def reset(self) -> None:
        self.requested.clear()
        self.uploaded.clear()
        self.heartbeats.clear()

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
        self.heartbeats.append({
            "stage": stage,
            "done": a[0] if a else kw.get("designs_completed", 0),
            "total": a[1] if len(a) > 1 else kw.get("designs_total", 0),
            "new_candidate": kw.get("new_candidate"),
        })

    @property
    def upload_names(self) -> list[str]:
        return [n for n, _ in self.uploaded]

    @property
    def streamed(self) -> list[dict]:
        return [
            hb["new_candidate"] for hb in self.heartbeats
            if hb["new_candidate"]
        ]


def _streamer(output_dir, num_designs=8):
    return pxd._DesignStreamer(
        output_dir, "https://hub.invalid/upload", "tok",
        "https://hub.invalid/webhooks", "job-1", num_designs,
    )


# ---------------------------------------------------------------------------
# _upload_one: one file per request, and it never raises.
# ---------------------------------------------------------------------------

def test_upload_one_signs_and_puts_a_single_file(tmp_path, monkeypatch):
    rec = _Recorder()
    rec.install(monkeypatch)
    src = tmp_path / "a.pdb"
    src.write_text("ATOM\n")

    assert pxd._upload_one("https://hub.invalid/upload", "tok", "a.pdb", str(src))
    assert rec.requested == ["a.pdb"]
    assert rec.upload_names == ["a.pdb"]


def test_upload_one_swallows_a_signing_failure(tmp_path, monkeypatch):
    """A refusal must not unwind: it is retried on the next tick."""
    src = tmp_path / "a.pdb"
    src.write_text("ATOM\n")

    def _boom(endpoint, token, filenames):
        raise RuntimeError("502 from the signing endpoint")

    monkeypatch.setattr(pxd, "request_upload_urls", _boom)
    assert pxd._upload_one("e", "t", "a.pdb", str(src)) is False


def test_upload_one_refuses_a_file_that_is_not_on_disk(tmp_path, monkeypatch):
    rec = _Recorder()
    rec.install(monkeypatch)

    assert pxd._upload_one("e", "t", "a.pdb", str(tmp_path / "gone.pdb")) is False
    assert rec.requested == []


def test_upload_one_requests_nothing_without_an_endpoint(tmp_path, monkeypatch):
    """Without an endpoint, no signing is attempted at all.

    run_webhook_tier takes it from
    ``job_payload.get("upload_urls_endpoint", "")``, so a payload missing the
    key yields an empty string rather than an error.
    """
    rec = _Recorder()
    rec.install(monkeypatch)
    src = tmp_path / "a.pdb"
    src.write_text("ATOM\n")

    assert pxd._upload_one("", "tok", "a.pdb", str(src)) is False
    assert rec.requested == []


# ---------------------------------------------------------------------------
# The completeness heuristic.
# ---------------------------------------------------------------------------

def test_a_pdb_without_its_end_record_is_not_complete(tmp_path):
    path = _write_design(str(tmp_path), "sample_0", end=False)
    assert pxd._looks_complete(path, time.time()) is False


def test_a_pdb_still_being_written_is_not_complete(tmp_path):
    """Fresh mtime: the file may still be growing."""
    path = _write_design(str(tmp_path), "sample_0", quiesced=False)
    assert pxd._looks_complete(path, time.time()) is False


def test_an_empty_file_is_not_complete(tmp_path):
    """A .cif, so only the size check can reject it: an empty .pdb is rejected
    by the END check instead, which leaves the size check unproven."""
    design_dir = tmp_path / "global_run_0" / "spec" / "seed_000" / "converted_pdbs"
    design_dir.mkdir(parents=True)
    path = design_dir / "sample_0.cif"
    path.write_bytes(b"")
    old = time.time() - (pxd._QUIESCE_SECONDS + 5)
    os.utime(path, (old, old))
    assert pxd._looks_complete(str(path), time.time()) is False


def _write_cif(design_dir, stem, truncate_to=1.0):
    """Write a gemmi-canonical mmCIF design, optionally cut short.

    Written by gemmi rather than by hand so the complete case is a file
    gemmi itself accepts; ``truncate_to`` is the fraction kept, which is
    what a design still being written looks like on disk.
    """
    design_dir.mkdir(parents=True, exist_ok=True)
    pdb = design_dir / f"{stem}.src.pdb"
    pdb.write_text(
        "ATOM      1  N   ALA A   1      11.104   6.134  -6.504  1.00  0.00           N\n"
        "ATOM      2  CA  ALA A   1      11.639   6.071  -5.147  1.00  0.00           C\n"
        "END\n"
    )
    structure = gemmi.read_structure(str(pdb))
    structure.setup_entities()
    text = structure.make_mmcif_document().as_string()
    path = design_dir / f"{stem}.cif"
    path.write_text(text[: int(len(text) * truncate_to)])
    old = time.time() - (pxd._QUIESCE_SECONDS + 5)
    os.utime(path, (old, old))
    return path


def test_a_quiesced_cif_that_parses_is_complete(tmp_path):
    """CIF has no END record, so the second signal is a gemmi parse."""
    design_dir = tmp_path / "global_run_0" / "spec" / "seed_000" / "converted_pdbs"
    path = _write_cif(design_dir, "sample_0")
    assert pxd._looks_complete(str(path), time.time()) is True


def test_a_cif_cut_off_mid_write_is_not_complete(tmp_path):
    """A stall longer than _QUIESCE_SECONDS used to ship this truncated.

    It stays shipped once it does: the path goes into ``shipped``, and the
    end-of-run upload loop reads that as ``already_streamed`` and uploads
    nothing for it. So the parse has to reject it here or not at all.
    """
    design_dir = tmp_path / "global_run_0" / "spec" / "seed_000" / "converted_pdbs"
    path = _write_cif(design_dir, "sample_0", truncate_to=0.5)
    assert path.stat().st_size > 0
    assert pxd._looks_complete(str(path), time.time()) is False



def test_only_files_in_a_design_directory_are_swept(tmp_path, monkeypatch):
    """find_design_files' layer-3 fallback rglobs everything mid-run.

    Before the first design is converted, layers 1 and 2 are empty and the
    fallback indexes any .pdb under the output dir -- including the copies
    the wrapper itself writes there. Streaming one would invent a design.
    """
    stray = tmp_path / "target_input.pdb"
    stray.write_text("ATOM\nEND\n")
    old = time.time() - (pxd._QUIESCE_SECONDS + 5)
    os.utime(stray, (old, old))
    assert pxd._in_design_dir(str(stray)) is False

    rec = _Recorder()
    rec.install(monkeypatch)
    streamer = _streamer(str(tmp_path))
    assert streamer.sweep() == (pxd._RUNNING_STAGE, 0)
    assert rec.upload_names == []


# ---------------------------------------------------------------------------
# The sweep.
# ---------------------------------------------------------------------------

def test_sweep_ships_each_finished_design_once(tmp_path, monkeypatch):
    rec = _Recorder()
    rec.install(monkeypatch)
    _write_design(str(tmp_path), "spec_sample_0")
    _write_design(str(tmp_path), "spec_sample_1")
    streamer = _streamer(str(tmp_path))

    stage, done = streamer.sweep()

    assert (stage, done) == (pxd._RUNNING_STAGE, 2)
    assert rec.upload_names == ["spec_sample_0.pdb", "spec_sample_1.pdb"]
    # One beat per design, each carrying the design it just landed.
    assert [c["pdb_key"] for c in rec.streamed] == [
        "spec_sample_0.pdb", "spec_sample_1.pdb",
    ]
    assert [c["rank"] for c in rec.streamed] == [1, 2]
    # No scores exist until summary.csv is written, and the hub drops a
    # partial's filter_status anyway (_candidate_from_partial).
    assert rec.streamed[0]["iptm"] is None
    assert rec.streamed[0]["plddt"] is None


def test_a_second_sweep_with_nothing_new_ships_nothing(tmp_path, monkeypatch):
    rec = _Recorder()
    rec.install(monkeypatch)
    _write_design(str(tmp_path), "spec_sample_0")
    streamer = _streamer(str(tmp_path))
    streamer.sweep()
    rec.reset()

    assert streamer.sweep() == (pxd._RUNNING_STAGE, 1)
    assert rec.requested == []
    assert rec.heartbeats == []


def test_an_unfinished_design_waits_for_a_later_sweep(tmp_path, monkeypatch):
    rec = _Recorder()
    rec.install(monkeypatch)
    path = _write_design(str(tmp_path), "spec_sample_0", end=False)
    streamer = _streamer(str(tmp_path))

    assert streamer.sweep() == (pxd._RUNNING_STAGE, 0)
    assert rec.upload_names == []

    with open(path, "a") as fh:
        fh.write("END\n")
    old = time.time() - (pxd._QUIESCE_SECONDS + 5)
    os.utime(path, (old, old))

    assert streamer.sweep() == (pxd._RUNNING_STAGE, 1)
    assert rec.upload_names == ["spec_sample_0.pdb"]


def test_a_failed_upload_is_retried_under_the_same_name(tmp_path, monkeypatch):
    rec = _Recorder(fail_first={"spec_sample_0.pdb"})
    rec.install(monkeypatch)
    _write_design(str(tmp_path), "spec_sample_0")
    streamer = _streamer(str(tmp_path))

    assert streamer.sweep() == (pxd._RUNNING_STAGE, 0)
    # Beaten even though nothing landed, so a storage outage cannot starve
    # the keepalive; but not offered, because the bytes are not there.
    assert len(rec.heartbeats) == 1
    assert rec.streamed == []

    assert streamer.sweep() == (pxd._RUNNING_STAGE, 1)
    assert rec.upload_names == ["spec_sample_0.pdb"]
    assert [c["pdb_key"] for c in rec.streamed] == ["spec_sample_0.pdb"]


def test_two_files_sharing_a_stem_get_distinct_names(tmp_path):
    """One name per design, or the second overwrites the first in Storage.

    Two seeds can hold the same stem. The sweep does not see both -- it
    collects through find_design_files, which indexes by stem with
    ``setdefault`` and so keeps only the first of two (pre-existing, not
    changed here) -- but resolve_design_local_path's chosen_struct_path
    branch can hand the upload loop a path that index never held, so the two
    name sources must not collide.
    """
    assigned: dict[str, str] = {}
    a = _write_design(str(tmp_path), "spec_sample_0", seed="seed_000")
    b = _write_design(str(tmp_path), "spec_sample_0", seed="seed_001")

    assert pxd._assign_upload_name(a, assigned) == "spec_sample_0.pdb"
    assert pxd._assign_upload_name(b, assigned) == "spec_sample_0__2.pdb"
    # Stable: the same path asks again and gets the name it already has.
    assert pxd._assign_upload_name(a, assigned) == "spec_sample_0.pdb"


def test_a_name_is_handed_out_under_the_lock(tmp_path, monkeypatch):
    """Both the sweep thread and the upload loop call this on one dict.

    The loop starts after ``keepalive_thread.join(timeout=180)``, which can
    return with the sweep still running, and read-then-write without the lock
    can hand two designs one name. Asserted by holding, not by racing: a race
    test would be flaky in both directions.
    """
    held = []
    real = pxd._NAME_LOCK

    class _Watched:
        def __enter__(self):
            held.append("in")
            return real.__enter__()

        def __exit__(self, *exc):
            held.append("out")
            return real.__exit__(*exc)

    monkeypatch.setattr(pxd, "_NAME_LOCK", _Watched())
    path = str(tmp_path / "sample_0.pdb")

    assert pxd._assign_upload_name(path, {}) == "sample_0.pdb"
    assert held == ["in", "out"]


def test_a_stop_lands_between_two_designs(tmp_path, monkeypatch):
    """The keepalive's stop is the streamer's, so a sweep gives up mid-list."""
    rec = _Recorder()
    rec.install(monkeypatch)
    _write_design(str(tmp_path), "spec_sample_0")
    _write_design(str(tmp_path), "spec_sample_1")
    streamer = _streamer(str(tmp_path))
    streamer.stop.set()

    assert streamer.sweep() == (pxd._RUNNING_STAGE, 0)
    assert rec.upload_names == []
