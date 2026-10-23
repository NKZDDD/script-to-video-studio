from pathlib import Path
import shutil
import subprocess

import pytest


def test_file_preview_javascript_behavior():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required for browser script behavior tests')
    subprocess.run([node, str(Path(__file__).with_suffix('.js'))], check=True, timeout=20)
