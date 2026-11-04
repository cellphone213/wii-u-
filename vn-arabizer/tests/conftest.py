import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))


def pytest_configure(config):
    # Keep tests from touching the real app data folder.
    os.environ.setdefault("VNARABIZER_HOME", str(Path(config.rootpath) / ".pytest-home"))
