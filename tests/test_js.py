import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def test_the_javascript_unit_tests_pass() -> None:
    result = subprocess.run(
        ["node", "--test", "tests/js/*.test.js"], capture_output=True, text=True, cwd=ROOT, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
