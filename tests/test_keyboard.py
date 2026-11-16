"""Mobile keyboard regressions exercise the real frontend controller in Node."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_mobile_keyboard_regressions():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js required for frontend keyboard checks')
    script = Path(__file__).resolve().parent / 'frontend/keyboard.test.cjs'
    result = subprocess.run([node, '--test', str(script)], text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
