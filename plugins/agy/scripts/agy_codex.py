"""Codex entry point for local Antigravity reviews."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import agy_companion
from agylib.state import SESSION_ID_ENV, STATE_DIR_ENV

if __name__ == "__main__":
    os.environ[STATE_DIR_ENV] = str(Path.home() / ".cache" / "agy-codex" / "state")
    os.environ.pop(SESSION_ID_ENV, None)
    sys.exit(agy_companion.main(codex=True))
