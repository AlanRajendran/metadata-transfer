# SPDX-License-Identifier: GPL-3.0-or-later
"""Test configuration.

Most tests use synthetic data made on the fly. Tests against real microscope files run when a
samples folder is available and skip otherwise (fresh clone, CI):

* ``METADATA_TRANSFER_SAMPLES``: a JSON file mapping sample roles to file paths, or a folder
  holding ``samples.json``. Default: ``samples.json`` next to the repository folder.
  Roles: lif, vsi_multichannel, vsi_timelapse, vsi_overview, nd2_zstack, nd2_timelapse,
  nd2_multipoint, nd2_large, golden_dir (ND2 files written by version 0.1 from the LIF).
  Relative paths are relative to the JSON file.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "source"))


def _samples() -> dict[str, str]:
    where = os.environ.get("METADATA_TRANSFER_SAMPLES") or os.path.join(os.path.dirname(REPO), "samples.json")
    if os.path.isdir(where):
        where = os.path.join(where, "samples.json")
    try:
        with open(where, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    base = os.path.dirname(os.path.abspath(where))
    return {k: os.path.normpath(os.path.join(base, v)) for k, v in data.items() if isinstance(v, str)}


SAMPLES = _samples()


def sample(role: str) -> str:
    path = SAMPLES.get(role)
    if not path or not os.path.exists(path):
        pytest.skip(f"sample '{role}' not available")
    return path


@pytest.fixture(scope="session")
def sample_lif() -> str:
    return sample("lif")


@pytest.fixture(scope="session")
def vsi_multichannel() -> str:
    return sample("vsi_multichannel")


@pytest.fixture(scope="session")
def vsi_timelapse() -> str:
    return sample("vsi_timelapse")


@pytest.fixture(scope="session")
def vsi_overview() -> str:
    return sample("vsi_overview")


@pytest.fixture(scope="session")
def golden_dir() -> str:
    return sample("golden_dir")


@pytest.fixture(autouse=True)
def _isolated_appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
