"""Codex entry point for local Antigravity reviews."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# The script's own directory is not on sys.path under `python3 -P` or PYTHONSAFEPATH.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import agy_companion  # noqa: E402
from agylib.state import SESSION_ID_ENV, STATE_DIR_ENV, codex_state_root  # noqa: E402

if __name__ == "__main__":
    os.environ[STATE_DIR_ENV] = str(codex_state_root())
    os.environ.pop(SESSION_ID_ENV, None)
    sys.exit(agy_companion.main(codex=True))
