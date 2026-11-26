"""Codex entry point for local Antigravity jobs."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# The script's own directory is not on sys.path under `python3 -P` or PYTHONSAFEPATH.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import agy_companion  # noqa: E402
from agylib.state import INHERITED_STATE_DIR_ENV, SESSION_ID_ENV, STATE_DIR_ENV, codex_state_root  # noqa: E402

if __name__ == "__main__":
    # Preserve the caller's state location for mount validation before selecting our own store.
    if inherited := os.environ.get(STATE_DIR_ENV):
        os.environ[INHERITED_STATE_DIR_ENV] = str(Path(inherited).resolve())
    os.environ[STATE_DIR_ENV] = str(codex_state_root())
    os.environ.pop(SESSION_ID_ENV, None)
    sys.exit(agy_companion.main(codex=True))
