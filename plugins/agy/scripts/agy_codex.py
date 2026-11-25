"""Codex entry point for local Antigravity reviews."""

from __future__ import annotations

import os
import sys

import agy_companion
from agylib.state import SESSION_ID_ENV, STATE_DIR_ENV, codex_state_root

if __name__ == "__main__":
    os.environ[STATE_DIR_ENV] = str(codex_state_root())
    os.environ.pop(SESSION_ID_ENV, None)
    sys.exit(agy_companion.main(codex=True))
