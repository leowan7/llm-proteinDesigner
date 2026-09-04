"""validate_preflight must REPORT bad params, not die on them.

The bounds loop compared the raw override value against a float. The
values arrive from the model's ``user_overrides`` and are copied through
verbatim by ``_handle_collect_parameters``, so a JSON null or a string
reaches the comparison -- and ``None < 0.0`` raises TypeError inside a
dispatcher with no try/except, killing the agent turn. A preflight whose
job is to turn bad input into a reported failure instead crashed on it.
"""
import pytest
from agent.tools import check_param_bounds
from agent.wizard import WIZARD_PARAMS


def _checks(tool: str, params: dict) -> list:
    return check_param_bounds(tool, params)


def _by_name(checks: list, name: str) -> dict:
    return next(c for c in checks if c["check_name"] == f"param_{name}")


@pytest.mark.parametrize("bad", [None, "", "abc", {}, []])
def test_a_non_numeric_override_is_a_failed_check_not_an_exception(bad):
    checks = _checks("rfdiffusion", {"noise_scale": bad})
    assert _by_name(checks, "noise_scale")["status"] == "fail"


def test_the_recommended_value_still_passes():
    """0.0 is falsy and sits exactly on min_value -- both easy to break."""
    assert _by_name(_checks("rfdiffusion", {"noise_scale": 0.0}),
                    "noise_scale")["status"] == "pass"


def test_a_numeric_string_is_accepted_rather_than_rejected():
    assert _by_name(_checks("rfdiffusion", {"noise_scale": "0.5"}),
                    "noise_scale")["status"] == "pass"


def test_real_bounds_violations_still_fail():
    assert _by_name(_checks("rfdiffusion", {"noise_scale": 5.0}),
                    "noise_scale")["status"] == "fail"


def test_every_bounded_wizard_param_survives_a_null():
    """Not just noise_scale: the crash was generic to the loop."""
    for tool, params in WIZARD_PARAMS.items():
        for p in params:
            if p.min_value is None and p.max_value is None:
                continue
            checks = _checks(tool, {p.name: None})
            assert _by_name(checks, p.name)["status"] == "fail", (
                f"{tool}.{p.name} did not report a null as a failure"
            )
