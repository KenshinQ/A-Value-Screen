
from __future__ import annotations

from pathlib import Path

import pytest

from avalue_screen.config import load_config
from tests.fixtures.generate_fixtures import write_universe

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def config():
    return load_config(ROOT / "configs" / "default.yaml")


@pytest.fixture(scope="session")
def universe_dir(tmp_path_factory):
    dest = tmp_path_factory.mktemp("universe")
    write_universe(Path(dest))
    return Path(dest)


@pytest.fixture(scope="session")
def repo_universe(tmp_path_factory):
    dest = ROOT / "tests" / "fixtures" / "universe"
    write_universe(dest)
    return dest
