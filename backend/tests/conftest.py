import sys
from pathlib import Path

import pytest

# Make `import app...` work when pytest runs from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def pytest_addoption(parser):
    parser.addoption("--run-slow", action="store_true", help="also run tests that hit real external APIs")


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: hits real external APIs over the network (run with --run-slow)")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-slow"):
        return
    skip = pytest.mark.skip(reason="network test; run with --run-slow")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip)
