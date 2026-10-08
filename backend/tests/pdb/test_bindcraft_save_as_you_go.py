"""Each accepted design reaches Storage while BindCraft is still running.

Three invariants, all load-bearing:

1. Designs are uploaded from the heartbeat tick, not only from main()'s
   final loop. A BindCraft pilot runs for up to four hours; a run that is
   cancelled, killed or times out before that loop delivers nothing it
   had already paid for. The hub keeps a streamed design only when its
   bytes are in Storage (``shared/job_recovery.py::reconstruct``), so an
   upload that never happened is GPU time no later join can recover.
2. No design is uploaded twice, and the two beats for one design carry
   the same ``pdb_key`` string. One failed name fails the whole signing
   request (``webhooks/uploads.py:104-118`` returns 502), and the signing
   call passes no upsert (``shared/storage.py:443-462``). That a bucket
   collision is what makes the name fail is UNVERIFIED against a live
   bucket: it is read from the no-upsert call, not observed refusing.
   The hub dedupes partials on the exact ``pdb_key``
   (``webhooks/modal.py::_hb_merge_inputs``), so two spellings of one
   design would list it twice regardless.
3. An early-ended run still FAILS, and its designs are delivered by
   having been uploaded mid-run rather than by rewriting the outcome.
   What that costs the customer no longer rides on the status alone: the
   webhook commit on this branch meters a failed run of a streaming tool
   once Storage holds one of its designs
   (``backend/webhooks/router.py``, ``storage/client.py``
   ``::job_has_saved_designs``). Reporting the run as COMPLETED would
   still send the completion email instead of the failure one, and would
   bill the saved-nothing run that stays free. The sweep must also be
   stopped before main()'s own upload loop starts, or the two sign one
   object name at once.

Its own file, like the sibling tool tests in this directory, so in-flight
branches do not collide at one file's end on merge.
"""
from __future__ import annotations

import csv
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

_REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..")
)
_RUN_PIPELINE = os.path.join(
    _REPO_ROOT, "docker", "bindcraft", "run_pipeline.py"
)


def _load_pipeline():
    if "bindcraft_run_pipeline" in sys.modules:
        return sys.modules["bindcraft_run_pipeline"]
    spec = importlib.util.spec_from_file_location(
        "bindcraft_run_pipeline", _RUN_PIPELINE
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bindcraft_run_pipeline"] = mod
    spec.loader.exec_module(mod)
    return mod


bc = _load_pipeline()

# (Accepted/*.pdb stem, final_design_stats.csv "Design" value, ipTM).
# The stem carries the _modelN suffix the CSV key does not -- the split
# parse_bindcraft_results makes. Sorted order is the glob order.
_DESIGNS = [
    ("design_l100_s1_mpnn1_model1", "design_l100_s1_mpnn1", 0.81),
    ("design_l100_s1_mpnn2_model3", "design_l100_s1_mpnn2", 0.62),
]


def _wait_until(predicate, what: str, timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    pytest.fail(f"timed out after {timeout}s waiting for {what}")


def _write_design(output_dir: str, stem: str) -> str:
    """Step one of BindCraft's two-step write: the PDB lands in Accepted/."""
    accepted = os.path.join(output_dir, "Accepted")
    os.makedirs(accepted, exist_ok=True)
    path = os.path.join(accepted, f"{stem}.pdb")
    with open(path, "w") as fh:
        fh.write(f"ATOM  {stem}\n")
    return path


def _write_stats(output_dir: str, rows: list[tuple[str, float]]) -> str:
    """Step two: the design's row appears in final_design_stats.csv.

    Rewritten whole rather than appended, which is indistinguishable from
    upstream's append as far as the reader is concerned.
    """
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, "final_design_stats.csv")
    columns = [
        "Rank", "Design", "Average_i_pTM", "Average_pLDDT",
        "Average_Binder_pAE",
    ]
    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        for i, (design_key, iptm) in enumerate(rows, start=1):
            writer.writerow({
                "Rank": i, "Design": design_key, "Average_i_pTM": iptm,
                "Average_pLDDT": 0.88, "Average_Binder_pAE": 9.5,
            })
    return path


class _Recorder:
    """Records every signing request, every PUT and every heartbeat."""

    def __init__(self, fail_first: set[str] | None = None):
        self.requested: list[str] = []
        self.uploaded: list[tuple[str, str]] = []
        self.heartbeats: list[dict] = []
        self.webhooks: list[dict] = []
        self._fail_first = set(fail_first or ())

    def install(self, monkeypatch) -> None:
        monkeypatch.setattr(bc, "request_upload_urls", self.request_upload_urls)
        monkeypatch.setattr(bc, "upload_output", self.upload_output)
        monkeypatch.setattr(bc, "send_heartbeat", self.send_heartbeat)

    def reset(self) -> None:
        """Clear what was observed, keeping the configured failures."""
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

    def post_webhook(self, webhook_url, job_id, pod_id, payload):
        self.webhooks.append(payload)

    @property
    def upload_names(self) -> list[str]:
        return [n for n, _ in self.uploaded]

    @property
    def streamed(self) -> list[dict]:
        return [
            hb["new_candidate"] for hb in self.heartbeats
            if hb["new_candidate"]
        ]


def _streamer(output_dir: str, num_designs: int = 2):
    return bc._DesignStreamer(
        output_dir, "https://hub.invalid/upload", "tok",
        "https://hub.invalid/webhooks", "job-1", num_designs,
    )


# ---------------------------------------------------------------------------
# 1. _upload_one: one file per request, and it never raises.
# ---------------------------------------------------------------------------

def test_upload_one_signs_and_puts_a_single_file(tmp_path, monkeypatch):
    rec = _Recorder()
    rec.install(monkeypatch)
    src = tmp_path / "a.pdb"
    src.write_text("ATOM\n")

    assert bc._upload_one("https://hub.invalid/upload", "tok", "a.pdb", str(src))
    assert rec.requested == ["a.pdb"]
    assert rec.upload_names == ["a.pdb"]


def test_upload_one_swallows_a_signing_failure(tmp_path, monkeypatch):
    """A refusal must not unwind: it is retried on the next tick."""
    src = tmp_path / "a.pdb"
    src.write_text("ATOM\n")

    def _boom(endpoint, token, filenames):
        raise RuntimeError("502 from the signing endpoint")

    monkeypatch.setattr(bc, "request_upload_urls", _boom)
    assert bc._upload_one("e", "t", "a.pdb", str(src)) is False


def test_upload_one_refuses_a_file_that_is_not_on_disk(tmp_path, monkeypatch):
    rec = _Recorder()
    rec.install(monkeypatch)

    assert bc._upload_one("e", "t", "a.pdb", str(tmp_path / "gone.pdb")) is False
    assert rec.requested == []


def test_upload_one_requests_nothing_without_an_endpoint(tmp_path, monkeypatch):
    """Without an endpoint, no signing is attempted at all.

    main() takes it from ``job_payload.get("upload_urls_endpoint", "")``,
    so a payload missing the key yields an empty string rather than an
    error, and every _upload_one call then has to refuse on its own.
    """
    rec = _Recorder()
    rec.install(monkeypatch)
    src = tmp_path / "a.pdb"
    src.write_text("ATOM\n")

    assert bc._upload_one("", "tok", "a.pdb", str(src)) is False
    assert rec.requested == []


# ---------------------------------------------------------------------------
# 2. _DesignStreamer.sweep: ships what is finished, once.
# ---------------------------------------------------------------------------

def test_sweep_ships_each_accepted_design_once(tmp_path, monkeypatch):
    rec = _Recorder()
    rec.install(monkeypatch)
    out = str(tmp_path / "outputs")
    for stem, _, _ in _DESIGNS:
        _write_design(out, stem)
    _write_stats(out, [(key, iptm) for _, key, iptm in _DESIGNS])

    streamer = _streamer(out)
    stage, shipped = streamer.sweep()

    assert (stage, shipped) == ("Running BindCraft", 2)
    assert rec.upload_names == [f"{d[0]}.pdb" for d in _DESIGNS]
    # One beat per design, so the gap between keepalives stays bounded by a
    # single file's upload rather than the whole tick's worth.
    assert len(rec.streamed) == len(_DESIGNS)
    assert [c["rank"] for c in rec.streamed] == [1, 2]
    assert [c["pdb_key"] for c in rec.streamed] == [
        f"{d[0]}.pdb" for d in _DESIGNS
    ]
    assert [c["iptm"] for c in rec.streamed] == [0.81, 0.62]
    assert all(c["filter_status"] == "pass" for c in rec.streamed)


def test_a_second_sweep_with_nothing_new_ships_nothing(tmp_path, monkeypatch):
    rec = _Recorder()
    rec.install(monkeypatch)
    out = str(tmp_path / "outputs")
    for stem, _, _ in _DESIGNS:
        _write_design(out, stem)
    _write_stats(out, [(key, iptm) for _, key, iptm in _DESIGNS])

    streamer = _streamer(out)
    streamer.sweep()
    rec.reset()

    assert streamer.sweep() == ("Running BindCraft", 2)
    assert rec.requested == []
    assert rec.upload_names == []
    assert rec.streamed == []


def test_a_design_without_its_stats_row_waits_for_it(tmp_path, monkeypatch):
    """The window between the two halves of upstream's write.

    BindCraft copies the PDB into Accepted/ and only then appends that
    design's row, so a PDB without a row may still be being written.
    """
    rec = _Recorder()
    rec.install(monkeypatch)
    out = str(tmp_path / "outputs")
    for stem, _, _ in _DESIGNS:
        _write_design(out, stem)
    _write_stats(out, [(_DESIGNS[0][1], _DESIGNS[0][2])])

    streamer = _streamer(out)
    streamer.sweep()
    assert rec.upload_names == [f"{_DESIGNS[0][0]}.pdb"]

    _write_stats(out, [(key, iptm) for _, key, iptm in _DESIGNS])
    rec.reset()
    streamer.sweep()

    assert rec.upload_names == [f"{_DESIGNS[1][0]}.pdb"]
    # Arrival order, not glob order: a recovered run reads this rank
    # verbatim (shared/job_recovery.py::_candidate_from_partial), and
    # arrival order is the spelling with no gaps in it.
    assert [c["rank"] for c in rec.streamed] == [2]


def test_a_failed_upload_is_retried_on_the_next_sweep(tmp_path, monkeypatch):
    rec = _Recorder(fail_first={f"{_DESIGNS[1][0]}.pdb"})
    rec.install(monkeypatch)
    out = str(tmp_path / "outputs")
    for stem, _, _ in _DESIGNS:
        _write_design(out, stem)
    _write_stats(out, [(key, iptm) for _, key, iptm in _DESIGNS])

    streamer = _streamer(out)
    assert streamer.sweep() == ("Running BindCraft", 1)
    # Not marked shipped, so main()'s final loop would still re-sign it.
    assert list(streamer.shipped) == [_DESIGNS[0][0]]
    assert [c["rank"] for c in rec.streamed] == [1]

    rec.reset()
    assert streamer.sweep() == ("Running BindCraft", 2)
    assert rec.upload_names == [f"{_DESIGNS[1][0]}.pdb"]
    assert [c["rank"] for c in rec.streamed] == [2]


def test_every_design_beats_even_when_its_upload_fails(tmp_path, monkeypatch):
    """The sweep must not be able to starve the keepalive it rides on.

    A beat only on success lets a storage outage with a healthy API run a
    whole tick in silence -- each hung PUT burning upload_output's 120 s
    timeout with no backoff and no per-tick cap -- which can outlast
    STALE_HEARTBEAT_SECONDS (= 1800, ``backend/worker/cleanup.py:50``) and
    have ``detect_stale_jobs`` kill a run that was working. One beat per
    design bounds the gap to a single upload attempt instead.
    """
    rec = _Recorder(fail_first={f"{stem}.pdb" for stem, _, _ in _DESIGNS})
    rec.install(monkeypatch)
    out = str(tmp_path / "outputs")
    for stem, _, _ in _DESIGNS:
        _write_design(out, stem)
    _write_stats(out, [(key, iptm) for _, key, iptm in _DESIGNS])

    assert _streamer(out).sweep() == ("Running BindCraft", 0)
    # Nothing landed, so nothing is offered to the 3D viewer ...
    assert rec.uploaded == []
    assert rec.streamed == []
    # ... but the cron still heard from us once per design.
    assert len(rec.heartbeats) == len(_DESIGNS)
    assert {hb["stage"] for hb in rec.heartbeats} == {"Running BindCraft"}


def test_a_sweep_stops_between_designs_once_stop_is_set(tmp_path, monkeypatch):
    """What bounds the join in _HeartbeatThread.stop() to one upload.

    Without the per-design check a sweep that still had fifty designs to
    ship would keep uploading long after stop() returned, alongside
    main()'s own upload loop.
    """
    rec = _Recorder()
    rec.install(monkeypatch)
    out = str(tmp_path / "outputs")
    for stem, _, _ in _DESIGNS:
        _write_design(out, stem)
    _write_stats(out, [(key, iptm) for _, key, iptm in _DESIGNS])

    streamer = _streamer(out)
    real_upload = rec.upload_output

    def _upload_then_stop(url, file_path):
        real_upload(url, file_path)
        streamer.stop.set()  # as if main() left the with-block right here

    monkeypatch.setattr(bc, "upload_output", _upload_then_stop)

    assert streamer.sweep() == ("Running BindCraft", 1)
    assert rec.upload_names == [f"{_DESIGNS[0][0]}.pdb"]
    assert list(streamer.shipped) == [_DESIGNS[0][0]]


# ---------------------------------------------------------------------------
# 3. The tick must not starve the keepalive it rides on.
# ---------------------------------------------------------------------------

def test_the_keepalive_beat_precedes_the_tick_callback(monkeypatch):
    """Ordering is the whole point: the callback does uploads.

    If it ran before the beat, a slow tick would postpone the keepalive
    the stale-job cron watches for
    (``backend/worker/cleanup.py::STALE_HEARTBEAT_SECONDS``).
    """
    events: list[tuple[str, str, int]] = []
    ticks: list[int] = []

    def _beat(url, job_id, stage, done=0, total=0, new_candidate=None):
        events.append(("beat", stage, done))

    def _tick():
        ticks.append(1)
        events.append(("tick", "", len(ticks)))
        return ("Running BindCraft", 1)

    monkeypatch.setattr(bc, "send_heartbeat", _beat)

    with bc._HeartbeatThread(
        "https://hub.invalid/webhooks", "job-1", stage="start",
        designs_total=2, interval_seconds=0.01, on_tick=_tick,
    ):
        _wait_until(lambda: len(ticks) >= 2, "two heartbeat ticks")

    assert events[0] == ("beat", "start", 0)
    assert events[1][0] == "tick"
    # ...and what the callback returned is what later beats report.
    assert ("beat", "Running BindCraft", 1) in events


def test_a_raising_tick_callback_does_not_stop_the_beats(monkeypatch):
    beats: list[str] = []

    def _beat(url, job_id, stage, done=0, total=0, new_candidate=None):
        beats.append(stage)

    def _tick():
        raise RuntimeError("the output dir vanished")

    monkeypatch.setattr(bc, "send_heartbeat", _beat)

    with bc._HeartbeatThread(
        "https://hub.invalid/webhooks", "job-1", stage="start",
        interval_seconds=0.01, on_tick=_tick,
    ):
        _wait_until(lambda: len(beats) >= 3, "three keepalive beats")

    assert set(beats) == {"start"}


def test_a_keepalive_beat_never_reports_more_designs_than_its_total(
    monkeypatch,
):
    """The keepalive beat's denominator has to follow the numerator up.

    ``on_tick`` replaces the stage and the completed count but not the total,
    so once BindCraft banks more designs than were requested the two beat
    sources would disagree: the sweep clamps its own denominator, this one
    kept the constructor's. The backend renders both into the same SSE
    string (``backend/webhooks/router.py:379``), so the live page would
    alternate between "11/10 designs" and "11/11 designs" every tick.
    """
    beats: list[tuple[int, int]] = []

    def _beat(url, job_id, stage, done=0, total=0, new_candidate=None):
        beats.append((done, total))

    # Two designs requested, three accepted -- the over-delivery case.
    monkeypatch.setattr(bc, "send_heartbeat", _beat)

    with bc._HeartbeatThread(
        "https://hub.invalid/webhooks", "job-1", stage="start",
        designs_total=2, interval_seconds=0.01,
        on_tick=lambda: ("Running BindCraft", 3),
    ):
        _wait_until(lambda: len(beats) >= 3, "three keepalive beats")

    assert any(done == 3 for done, _ in beats), f"tick never applied: {beats}"
    assert all(total >= done for done, total in beats), (
        f"a beat reported more designs than its total: {beats}"
    )


# ---------------------------------------------------------------------------
# 4. A whole run: both halves in one pass.
# ---------------------------------------------------------------------------

def _run_main(
    tmp_path, monkeypatch, rec, *,
    designs=_DESIGNS, run_raises=None, stream_at_least=None,
):
    """Drive main()'s production path against fakes for everything it calls out to.

    Replaces the GPU subprocess, startup_check, download_input,
    archive_work_dir, request_upload_urls, upload_output, send_heartbeat and
    post_webhook, and wraps write_bindcraft_settings. The tick sweep and the
    final upload loop are both the real ones -- a double upload can only be
    seen with the two running in one pass.
    """
    advanced = tmp_path / "advanced.json"
    advanced.write_text(json.dumps({"max_trajectories": False}))
    monkeypatch.setattr(bc, "BINDCRAFT_ADVANCED", str(advanced))
    monkeypatch.setattr(bc, "startup_check", lambda: {})
    monkeypatch.setattr(bc, "archive_work_dir", lambda *a, **kw: None)
    monkeypatch.setattr(
        bc, "download_input",
        lambda url, dest: Path(dest).write_text("ATOM  target\n"),
    )
    rec.install(monkeypatch)
    monkeypatch.setattr(bc, "post_webhook", rec.post_webhook)
    monkeypatch.syspath_prepend(_REPO_ROOT)  # contracts/, at /opt in the image

    real_settings = bc.write_bindcraft_settings

    def _settings(job_spec, target_pdb_path, output_dir):
        # Lay the output tree down here, before the heartbeat thread
        # starts, so one tick deterministically has designs to sweep.
        path = real_settings(job_spec, target_pdb_path, output_dir)
        for stem, _, _ in designs:
            _write_design(output_dir, stem)
        _write_stats(output_dir, [(k, i) for _, k, i in designs])
        return path

    monkeypatch.setattr(bc, "write_bindcraft_settings", _settings)

    def _fake_run(cmd, timeout=None, cwd=None):
        # Hold the subprocess open until the tick has shipped what is on
        # disk; this is the mid-run half. stream_at_least is lower than the
        # design count for the cases where a design is meant NOT to stream:
        # the retry is a tick away (60 s) and no test waits that long.
        want = len(designs) if stream_at_least is None else stream_at_least
        _wait_until(
            lambda: len(rec.streamed) >= want,
            f"the tick to ship {want} design(s) on disk",
        )
        if run_raises is not None:
            raise run_raises
        return ""

    monkeypatch.setattr(bc, "run_command", _fake_run)

    monkeypatch.setenv("JOB_PAYLOAD", json.dumps({
        "job_id": "job-1",
        "job_spec": {
            "target_chain": "A", "job_tier": "pilot",
            "parameters": {"num_designs": len(designs)},
        },
        "input_presigned_url": "https://storage.invalid/target.pdb",
        "upload_urls_endpoint": "https://hub.invalid/upload",
    }))
    monkeypatch.setenv("JOB_TOKEN", "test-token")
    monkeypatch.setenv("WEBHOOK_URL", "https://hub.invalid/webhooks")
    monkeypatch.setenv("JOB_ID", "job-1")

    bc.main()
    assert rec.webhooks, "main() posted no webhook"
    return rec.webhooks[-1]


def test_no_design_is_uploaded_twice_in_a_whole_run(tmp_path, monkeypatch):
    rec = _Recorder()
    payload = _run_main(tmp_path, monkeypatch, rec)

    expected = sorted(f"{d[0]}.pdb" for d in _DESIGNS)
    assert sorted(n for n in rec.requested if n.endswith(".pdb")) == expected
    assert sorted(n for n in rec.upload_names if n.endswith(".pdb")) == expected
    assert rec.upload_names.count("metrics.csv") == 1
    assert "failed_uploads" not in payload

    assert sorted(c["pdb_key"] for c in payload["candidates"]) == sorted(
        f"designs/{d[0]}.pdb" for d in _DESIGNS
    )
    # Each design is offered as a candidate exactly once. The tick streams
    # it; the final loop then skips it rather than re-offering the same
    # pdb_key under its own, different rank.
    keys = [c["pdb_key"] for c in rec.streamed]
    assert len(keys) == len(_DESIGNS)
    assert sorted(keys) == expected
    # ...and that string is the basename of the final key, which is the
    # half shared/job_recovery.py::reconstruct matches on.
    assert set(keys) == {
        os.path.basename(c["pdb_key"]) for c in payload["candidates"]
    }


def test_the_progress_count_never_goes_backwards(tmp_path, monkeypatch):
    """The design counter is user-visible, so it has to be monotonic.

    ``backend/webhooks/router.py:377-380`` renders designs_completed /
    designs_total into ``jobs.stage`` and :393-403 pushes that to the live
    page. Once the tick sweep reports n designs, no later beat may report
    fewer. Nothing streamed before this change, so this is not a regression
    guard: it pins a jump an intermediate version of the streaming diff
    introduced, where the upload phase restarted at 1 against its own
    ``len(candidates)`` denominator while the sweep had already reported the
    whole set.
    """
    rec = _Recorder()
    _run_main(tmp_path, monkeypatch, rec)

    counts = [hb["done"] for hb in rec.heartbeats]
    assert counts == sorted(counts), f"progress went backwards: {counts}"
    # The run really did stream, so this is not vacuous.
    assert max(counts) == len(_DESIGNS)
    assert {hb["total"] for hb in rec.heartbeats} == {len(_DESIGNS)}


def test_two_designs_are_never_offered_under_one_rank(tmp_path, monkeypatch):
    """One live rank must never name two designs.

    tools-hub's live table keys its rows on this rank and overwrites the row
    it finds (``templates/job_detail.html:632,681``), so a repeat erases a
    design the user has already paid for while the partial count still
    includes both.

    The two phases number on different scales -- the sweep by arrival, the
    final loop by BindCraft's sorted filename order -- so the collision needs
    a design that never streamed and sorts above one that did. Here the
    lower-sorting design's mid-run upload fails, leaving the higher-sorting
    one holding arrival rank 1; the final loop then uploads the first one
    successfully, and numbering it by sorted rank would hand it that same 1.
    """
    first_pdb = f"{_DESIGNS[0][0]}.pdb"
    rec = _Recorder(fail_first={first_pdb})
    _run_main(tmp_path, monkeypatch, rec, stream_at_least=1)

    offered = rec.streamed
    assert len(offered) == len(_DESIGNS), "every design is offered once"
    ranks = [c["rank"] for c in offered]
    assert len(set(ranks)) == len(ranks), f"a rank names two designs: {offered}"
    # The scenario really did split across the two phases, so the unique
    # ranks above are not just one phase's own counter: the design that
    # sorts first is the one that failed mid-run, so it is offered last.
    assert offered[0]["pdb_key"] != first_pdb
    assert offered[-1]["pdb_key"] == first_pdb


@pytest.mark.parametrize("raised", [
    subprocess.TimeoutExpired(cmd=["bindcraft.py"], timeout=14400),
    RuntimeError("Command failed (exit 1): CUDA OOM"),
])
def test_an_early_ended_run_still_fails_but_its_designs_are_in_storage(
    tmp_path, monkeypatch, raised,
):
    """The streamed uploads must NOT be bought by flipping the run's status.

    A run that dies early stays a failure. ``backend/webhooks/router.py``
    maps FAILED and TIMED_OUT to ``"failed"`` (``_RUNPOD_STATUS_MAP``), and
    since the webhook commit on this branch it meters a failed run of a tool
    in ``_STREAMS_DESIGNS_MID_RUN`` once Storage holds one of its designs --
    so this run is settled on GPU time used whichever status it reports, and
    flipping the status buys nothing. What flipping would still do is send the
    completion email instead of the failure one, present a crashed run as a
    success, and bill a run that saved nothing, which stays free. The designs
    are delivered by having been uploaded mid-run, not by rewriting the
    outcome.
    """
    rec = _Recorder()
    payload = _run_main(tmp_path, monkeypatch, rec, run_raises=raised)

    assert "candidates" not in payload
    assert "error" in payload
    # ...and yet every design is in Storage, put there by the tick.
    assert sorted(n for n in rec.upload_names if n.endswith(".pdb")) == sorted(
        f"{d[0]}.pdb" for d in _DESIGNS
    )
    assert len(rec.streamed) == len(_DESIGNS)


def test_stop_waits_for_an_in_flight_sweep(monkeypatch):
    """__exit__ waits for a sweep that finishes inside the join budget.

    main()'s final loop signs object names for designs not in
    ``streamer.shipped``, and a sweep sets that only after its upload
    returns, so the two running at once can sign one name twice. Whether
    the duplicate signing is actually refused is UNVERIFIED (see
    ``_upload_one``); not overlapping them is the cheaper guarantee.

    This pins the waiting, not a guarantee of no overlap: the 180s join is
    not a wall-clock bound on a PUT (see ``_HeartbeatThread.stop``), so a
    trickling upload can outlast it. The tick here takes 0.5s, well inside
    the budget.
    """
    monkeypatch.setattr(bc, "send_heartbeat", lambda *a, **kw: None)
    state = {"in_tick": False, "ticks": 0}

    def _tick():
        state["in_tick"] = True
        state["ticks"] += 1
        time.sleep(0.5)
        state["in_tick"] = False
        return ("Running BindCraft", state["ticks"])

    hb = bc._HeartbeatThread(
        "https://hub.invalid/webhooks", "job-1", stage="start",
        interval_seconds=0.01, on_tick=_tick,
    )
    hb.start()
    _wait_until(lambda: state["in_tick"], "a sweep to start")
    hb.stop()

    assert not state["in_tick"], "stop() returned mid-sweep"
    ticks_at_stop = state["ticks"]
    time.sleep(0.2)
    assert state["ticks"] == ticks_at_stop, "a sweep started after stop()"


def test_a_join_that_times_out_is_not_silent(caplog):
    """A sweep outlasting the join must leave a trace.

    The 180s join is not a wall-clock cap on a PUT, so an upload against a
    trickling endpoint can still be running when stop() returns -- and then
    main()'s final loop signs that object name alongside it. Nothing can
    stop that from here, but it must not be invisible when it happens.
    """
    class _Hung:
        def join(self, timeout=None):
            return None

        def is_alive(self):
            return True

    hb = bc._HeartbeatThread("https://hub.invalid/webhooks", "job-1", stage="start")
    hb._thread = _Hung()

    with caplog.at_level("WARNING"):
        hb.stop()

    assert any(
        "may overlap the final upload loop" in r.getMessage()
        for r in caplog.records
    ), "a timed-out join logged nothing"


def test_a_stop_during_a_beat_starts_no_further_sweep(monkeypatch):
    """The window the stop check in _run covers.

    send_heartbeat has its own request timeout, so a stop can land while
    the beat is blocked. Without the re-check a whole fresh sweep -- every
    remaining upload -- would start after stop() had already returned.
    """
    state = {"in_beat": False, "ticks": 0}

    def _slow_beat(*a, **kw):
        state["in_beat"] = True
        time.sleep(0.3)
        state["in_beat"] = False

    def _tick():
        state["ticks"] += 1
        return ("Running BindCraft", state["ticks"])

    monkeypatch.setattr(bc, "send_heartbeat", _slow_beat)

    hb = bc._HeartbeatThread(
        "https://hub.invalid/webhooks", "job-1", stage="start",
        interval_seconds=0.01, on_tick=_tick,
    )
    hb.start()
    _wait_until(lambda: state["in_beat"], "the first beat to block")
    hb.stop()

    assert state["ticks"] == 0, "a sweep ran after the stop landed in the beat"


# ---------------------------------------------------------------------------
# 5. The premise the failure path rests on.
# ---------------------------------------------------------------------------

def test_a_timeout_is_not_a_runtime_error():
    """Nothing catches either around run_command; this records only that
    they are distinct types arriving at the same catch-all."""
    assert not issubclass(subprocess.TimeoutExpired, RuntimeError)


def test_run_command_surfaces_a_timeout_unconverted():
    """Unlike docker/rfdiffusion/run_pipeline.py's run_command, this one does
    not convert a timeout to RuntimeError. Either way it leaves the
    _HeartbeatThread's with-block, which is what stops the sweep."""
    with pytest.raises(subprocess.TimeoutExpired):
        bc.run_command(
            [sys.executable, "-c", "import time; time.sleep(30)"], timeout=1,
        )


def test_run_command_still_raises_runtime_error_on_a_nonzero_exit():
    with pytest.raises(RuntimeError) as caught:
        bc.run_command([sys.executable, "-c", "raise SystemExit(3)"], timeout=60)
    assert "exit 3" in str(caught.value)
