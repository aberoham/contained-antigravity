"""Shell text that Claude or the user runs: the command files' companion calls and signin.sh's sign-in check."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys

import pytest
from agylib import agybin
from conftest import REPO

PLUGIN = REPO / "plugins" / "agy"
COMPANION_CALL = 'python3 "${CLAUDE_PLUGIN_ROOT}/scripts/agy_companion.py"'
PRINT_ARGV = f"{sys.executable} -c 'import json, sys; print(json.dumps(sys.argv[1:]))'"
SHELLS = [shell for shell in ("bash", "zsh") if shutil.which(shell)]
CALLER_IDS = ["claude-opus-5-5[1m]", "<your model ID>"]


def caller_model_lines() -> list[tuple[str, str]]:
    """Every companion command line in the command files that passes --caller-model."""
    found = []
    for path in sorted((PLUGIN / "commands").glob("*.md")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if COMPANION_CALL in line and "--caller-model" in line:
                inner = re.search(r"command: `(.*?)`", line)  # the background form inside Bash({...})
                found.append((path.name, inner.group(1) if inner else line))
    return found


def test_the_command_files_pass_the_caller_model():
    names = {name for name, _ in caller_model_lines()}
    assert names == {"review.md", "adversarial-review.md", "plan.md"}


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("caller", CALLER_IDS)
@pytest.mark.parametrize(("name", "line"), caller_model_lines())
def test_a_caller_model_and_space_path_reach_the_companion_from_extracted_lines(shell, caller, name, line, tmp_path):
    from agy_companion import build_parser, split_arguments

    file_flag = (
        '--request-file "packet folder/req.md"' if name == "plan.md" else '--instructions-file "packet folder/in.md"'
    )
    prose = 'check the caller\'s fix for #91 distinguishes {"id":"1"} from {"id":1}'
    escaped_prose = prose.replace("\\", "\\\\").replace('"', '\\"')
    command = (
        line.replace(COMPANION_CALL, PRINT_ARGV)
        .replace("<your model ID>", caller)
        .replace("<QUOTED_FLAGS>", file_flag)
        .replace("<QUOTED_FLAGS_WITHOUT_BACKGROUND>", file_flag)
        .replace("<ESCAPED_REQUEST>", escaped_prose)
        .replace("<ESCAPED_INSTRUCTIONS>", escaped_prose)
        .replace("<ESCAPED_FOCUS>", escaped_prose)
    )
    proc = subprocess.run(
        [shell, "-c", command],
        cwd=tmp_path,  # an unquoted [1m] glob would fail in zsh and might match a file in bash
        env={"PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, (name, proc.stderr)
    argv = json.loads(proc.stdout)
    assert argv[argv.index("--caller-model") + 1] == caller, name
    parsed = build_parser().parse_args(split_arguments(argv))
    if name == "plan.md":
        assert parsed.request_file == "packet folder/req.md"
        assert " ".join(parsed.request) == prose
    else:
        assert parsed.instructions_file == "packet folder/in.md"
        assert " ".join(parsed.focus) == prose


def signin_check_snippet() -> str:
    """The Python that containers/signin.sh runs to judge the container's `agy models` output."""
    script = (REPO / "containers" / "signin.sh").read_text(encoding="utf-8")
    match = re.search(r"/usr/bin/python3 -c '(\nimport sys\n.*?judge_models_listing.*?)'", script, re.S)
    assert match, "containers/signin.sh no longer judges the listing with agybin.judge_models_listing"
    return match.group(1)


LISTING = (
    "Fetching available models...\n"
    "gemini-4-neon-medium\tGemini 4 Neon (Medium)\n"
    "gemini-pro-orchestrator\tOrchestrator\n"
)


@pytest.mark.parametrize(
    ("status", "output", "signed_in"),
    [
        (0, LISTING, True),
        (0, "Fetching available models...\nError: Please sign in to view available models.", False),
        (0, "gemini-client: request failed", False),
        (1, "gemini-4-neon-low\tGemini 4 Neon (Low)", False),
        (0, "gemini-4-neon-low\tGemini 4 Neon (Low)\nPERMISSION_DENIED", False),
        (0, "", False),
    ],
)
def test_signin_script_check_follows_the_companion_rule(status, output, signed_in):
    proc = subprocess.run(
        [sys.executable, "-c", signin_check_snippet(), str(REPO), str(status)],
        input=output,
        capture_output=True,
        text=True,
        check=False,
    )
    assert (proc.returncode == 0) is signed_in, proc.stderr
    assert (agybin.judge_models_listing(status, output)[0] is True) is signed_in
