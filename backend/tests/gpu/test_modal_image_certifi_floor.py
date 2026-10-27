"""Every Modal GPU image installs the certifi floor into the Python env that runs run_pipeline.py."""
import re
from pathlib import Path

import pytest

_MODAL_DIR = Path(__file__).resolve().parents[3] / "infrastructure" / "modal"
_CERTIFI = '"certifi==2026.10.23"'

# Installer per tool, targeting the env of each app's _PYTHON. rfantibody's _PYTHON is
# "python3", which resolves to /opt/rfantibody/.venv/bin/python because
# docker/rfantibody/Dockerfile.modal:55 puts the venv first on PATH.
_INSTALLER = {
    "rfdiffusion": "python3 -m pip install",
    "pxdesign": "python3 -m pip install",
    "boltzgen": "python3 -m pip install",
    "bindcraft": "/miniforge3/envs/BindCraft/bin/pip install",
    "rfantibody": "uv pip install --no-cache --python /opt/rfantibody/.venv/bin/python",
}


def test_every_modal_app_is_covered():
    assert {p.stem.removesuffix("_app") for p in _MODAL_DIR.glob("*_app.py")} == set(_INSTALLER)


@pytest.mark.parametrize("tool", sorted(_INSTALLER))
def test_image_installs_certifi_floor(tool):
    src = (_MODAL_DIR / f"{tool}_app.py").read_text(encoding="utf-8")
    commands = re.findall(r"\.run_commands\('([^']*)'\)", src)
    assert any(c.startswith(_INSTALLER[tool]) and c.endswith(_CERTIFI) for c in commands), commands
