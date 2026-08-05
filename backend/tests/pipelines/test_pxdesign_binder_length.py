"""Type contract for PXDesign's ``binder_length``.

PXDesign is the only binder tool whose upstream YAML takes a SCALAR int —
RFdiffusion, BoltzGen and BindCraft all take ``{"min": .., "max": ..}`` and
guard with an ``isinstance(.., dict)`` check. PXDesign had no such guard, and
on the pilot path ``run_webhook_tier`` does not pass ``binder_length``, so an
untyped caller value fell straight through to spec.yaml. These tests pin both
the guard and the fact that ``build_yaml_spec`` actually calls it.

Loading note: ``docker/pxdesign/run_pipeline.py`` is a container entrypoint,
not an installed package, so it is loaded by path — inside a fixture rather
than at import time. That matters: the module has a top-level ``import
requests`` and does ``sys.path.insert(0, "/opt")``. Loading at collection
would make a missing/broken import abort the WHOLE backend session (pytest
stops on collection errors) and would leak "/opt" onto sys.path for every
later test. The fixture contains both. This is the only test that reaches
into ``docker/`` — CI runs pytest from ``backend/`` and does not otherwise
cover the container entrypoints.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_RUN_PIPELINE = _REPO_ROOT / "docker" / "pxdesign" / "run_pipeline.py"


@pytest.fixture(scope="module")
def px_module():
    """Load the PXDesign container entrypoint without leaking its side effects."""
    if not _RUN_PIPELINE.is_file():
        pytest.fail(f"container entrypoint not found at {_RUN_PIPELINE}")
    saved_path = list(sys.path)
    try:
        spec = importlib.util.spec_from_file_location(
            "pxdesign_run_pipeline", _RUN_PIPELINE
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        # The module inserts "/opt" for its in-container imports; on a dev box
        # or CI runner that would shadow site-packages for the rest of the run.
        sys.path[:] = saved_path


@pytest.fixture(scope="module")
def coerce(px_module):
    return px_module.coerce_binder_length


@pytest.mark.parametrize(
    "value,expected",
    [
        (80, 80),
        (150, 150),
        (1, 1),
        ("80", 80),          # an LLM- or YAML-authored params dict can carry a string
        (" 80 ", 80),
        ("+80", 80),
        (80.0, 80),          # an untyped JSON body can deliver a whole float
    ],
)
def test_accepts_scalar_lengths(coerce, value, expected):
    assert coerce(value) == expected


def test_accepted_values_are_always_int(coerce):
    """PXDesign writes this into YAML; a str would serialize as '80', not 80."""
    for value in (80, "80", 80.0):
        assert type(coerce(value)) is int


@pytest.mark.parametrize(
    "value",
    [
        {"min": 50, "max": 100},   # the RFdiffusion/BoltzGen/BindCraft shape
        {},
        [60, 120],
        (60, 120),
        None,
        "eighty",
        "80.5",
        80.5,                      # upstream would silently truncate to 80
    ],
)
def test_rejects_non_scalar_shapes(coerce, value):
    with pytest.raises(ValueError):
        coerce(value)


def test_dict_error_names_the_range_and_the_alternative(coerce):
    """The operator-facing message has to say what to do, not just 'invalid'."""
    with pytest.raises(ValueError) as exc:
        coerce({"min": 50, "max": 100})
    message = str(exc.value)
    assert "min/max" in message
    assert "single integer" in message


@pytest.mark.parametrize("value", [True, False])
def test_rejects_bool(coerce, value):
    """``isinstance(True, int)`` is True in Python, so bool needs its own gate.

    Upstream parses the field as ``int(cfg["binder_length"])``, which turns
    True into 1 — a run that succeeds with a 1-residue binder.
    """
    with pytest.raises(ValueError):
        coerce(value)


@pytest.mark.parametrize("value", [0, -5, "-5", "0"])
def test_rejects_non_positive(coerce, value):
    """Upstream accepts these at parse time, then dies in the GPU stage.

    They produce an empty design chain, so the failure surfaces late as
    "produced no summary.csv" rather than as a bad length.
    """
    with pytest.raises(ValueError):
        coerce(value)


@pytest.mark.parametrize("value", [80, "80", 80.0, 150, 1])
def test_never_disagrees_with_upstream_on_accepted_input(coerce, value):
    """The guard must not change the length upstream would have used.

    Upstream is ``binder_length = int(cfg["binder_length"])``. For anything
    this guard accepts, its answer has to match — otherwise the guard would
    silently redesign at a different length than callers got before it existed.
    """
    assert coerce(value) == int(value)


# ---------------------------------------------------------------------------
# Wiring: the guard is worthless if build_yaml_spec stops calling it.
# ---------------------------------------------------------------------------

def test_build_yaml_spec_rejects_dict_binder_length(px_module):
    """Deleting the coerce call site must fail here, not pass silently.

    The guard runs before ``get_chain_length``, so this needs no CIF fixture.
    """
    with pytest.raises(ValueError):
        px_module.build_yaml_spec(
            {"parameters": {"binder_length": {"min": 50, "max": 100}}},
            "unused.cif",
        )


def test_build_yaml_spec_emits_scalar_int(px_module, monkeypatch):
    """A string length must reach spec.yaml as an int, not as '80'."""
    monkeypatch.setattr(px_module, "get_chain_length", lambda *a, **k: 116)
    spec = px_module.build_yaml_spec(
        {"parameters": {"binder_length": "80"}}, "unused.cif"
    )
    assert spec["binder_length"] == 80
    assert type(spec["binder_length"]) is int
