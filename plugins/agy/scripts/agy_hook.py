#!/usr/bin/env python3
"""agy PreToolUse hook for one plugin job.

Usage: agy_hook.py <job-dir> [<log-dir>]

A host job writes this into its working copy's .agents/hooks.json; a
container job mounts it, read-only, as the hooks.json in the job's home.

Reads agy's hook payload on stdin, decides it with <job-dir>/policy.json and
prints the decision. Each decision is logged in <log-dir>, which defaults to
<job-dir>; a container job passes the one job subdirectory it may write,
because it sees only policy.json from the job directory. Any error denies
the call, so a broken hook cannot widen what agy may do.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agylib.policy import decide  # noqa: E402


def append_line(path: Path, record: dict) -> None:
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")


def main() -> int:
    try:
        job_dir = Path(sys.argv[1])
        log_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else job_dir
        payload = json.load(sys.stdin)
        policy = json.loads((job_dir / "policy.json").read_text(encoding="utf-8"))
        result = decide(payload, policy)
        call = payload.get("toolCall") or {}
        append_line(
            log_dir / "hook.ndjson",
            {
                "t": time.time(),
                "tool": call.get("name"),
                "decision": result.decision,
                "reason": result.reason,
            },
        )
        if result.queued_command is not None:
            append_line(
                log_dir / "queued.ndjson",
                {
                    "t": time.time(),
                    "command": result.queued_command,
                    "cwd": (call.get("args") or {}).get("Cwd"),
                    "source": "hook",
                },
            )
        print(json.dumps({"decision": result.decision, "reason": result.reason}))
    except Exception as exc:  # noqa: BLE001 - fail closed on anything
        print(json.dumps({"decision": "deny", "reason": f"agy plugin hook error: {exc}"}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
