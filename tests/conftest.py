"""Test-session isolation: keep the studio's per-user data (settings, caches) out of the real profile."""

import os
import tempfile

_ISOLATED_DATA_DIR = tempfile.mkdtemp(prefix="supercut_test_data_")
os.environ.setdefault("VKS_DATA_DIR", _ISOLATED_DATA_DIR)
