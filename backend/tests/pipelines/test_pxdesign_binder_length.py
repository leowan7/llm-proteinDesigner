"""Type contract for PXDesign's ``binder_length``.

PXDesign is the only binder tool whose upstream YAML takes a SCALAR int —
RFdiffusion, BoltzGen and BindCraft all take ``{"min": .., "max": ..}`` and
guard with an ``isinstance(.., dict)`` check. PXDesign had no such guard, and
on the pilot path ``run_webhook_tier`` does not pass ``binder_length``, so an
untyped caller value fell straight through to spec.yaml. These tests pin the
guard that closes that gap.

Loading note: ``docker/pxdesign/run_pipeline.py`` is a container entrypoint,
not an installed package, so it is loaded by path. Its module-level imports
are stdlib plus ``requests``/``yaml``, and its ``sys.path.insert("/opt")`` is
inert off-container, so this import is safe in a dev venv and in CI. This is
the first test to reach into ``docker/`` — CI runs pytest from ``backend/``
and does not otherwise cover that tree.
"""

import importlib.util
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_RUN_PIPELINE = _REPO_ROOT / "docker" / "pxdesign" / "run_pipeline.py"


def _load_coerce():
    spec = importlib.util.spec_from_file_location(
        "pxdesign_run_pipeline", _RUN_PIPELINE
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.coerce_binder_length


coerce_binder_length = _load_coerce()


def test_module_under_test_exists():
    """Guard against a silent skip if the container script is moved."""
    assert _RUN_PIPELINE.is_file(), f"missing {_RUN_PIPELINE}"


@pytest.mark.parametrize(
    "value,expected",
    [
        (80, 80),
        (150, 150),
        (1, 1),
        ("80", 80),          # stored job params round-trip through form posts
        (" 80 ", 80),
        ("+80", 80),
        (80.0, 80),          # an untyped JSON body can deliver a whole float
    ],
)
def test_accepts_scalar_lengths(value, expected):
    assert coerce_binder_length(value) == expected


def test_accepted_values_are_always_int():
    """PXDesign writes this into YAML; a str would serialize as '80', not 80."""
    for value in (80, "80", 80.0):
        assert type(coerce_binder_length(value)) is int


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
def test_rejects_non_scalar_shapes(value):
    with pytest.raises(ValueError):
        coerce_binder_length(value)


def test_dict_error_names_the_range_and_the_alternative():
    """The operator-facing message has to say what to do, not just 'invalid'."""
    with pytest.raises(ValueError) as exc:
        coerce_binder_length({"min": 50, "max": 100})
    message = str(exc.value)
    assert "min/max" in message
    assert "single integer" in message


@pytest.mark.parametrize("value", [True, False])
def test_rejects_bool(value):
    """``isinstance(True, int)`` is True in Python, so bool needs its own gate.

    Upstream parses the field as ``int(cfg["binder_length"])``, which turns
    True into 1 — a run that succeeds with a 1-residue binder.
    """
    with pytest.raises(ValueError):
        coerce_binder_length(value)


@pytest.mark.parametrize("value", [0, -5, "-5", "0"])
def test_rejects_non_positive(value):
    """Upstream's ``int()`` accepts these silently and designs nothing useful."""
    with pytest.raises(ValueError):
        coerce_binder_length(value)


@pytest.mark.parametrize("value", [80, "80", 80.0, 150, 1])
def test_never_disagrees_with_upstream_on_accepted_input(value):
    """The guard must not change the length upstream would have used.

    Upstream is ``binder_length = int(cfg["binder_length"])``. For anything
    this guard accepts, its answer has to match — otherwise the guard would
    silently redesign at a different length than callers got before it existed.
    """
    assert coerce_binder_length(value) == int(value)
