"""Codex loads its review skill and uses its own job store across invocations."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import agy_companion
import pytest
from agy_companion import build_parser
from agylib import render
from conftest import REPO, SCRIPTS


def test_codex_package():
    plugin = REPO / "plugins" / "agy"
    manifest = json.loads((plugin / ".codex-plugin/plugin.json").read_text())
    assert json.loads((plugin / manifest["hooks"]).read_text()) == {"hooks": {}}
    (skill,) = (plugin / manifest["skills"]).glob("*/SKILL.md")
    assert (skill.parent / "../../scripts/agy_codex.py").resolve().is_file()
    assert "name: agy-review" in skill.read_text()


@pytest.mark.parametrize("args", [["status"], ["cancel", "review-2"], ["result"], ["apply"], ["task"], ["resume"]])
def test_codex_rejects_ambiguous_or_unsupported_commands(args):
    with pytest.raises(SystemExit) as error:
        build_parser(codex=True).parse_args(args)
    assert error.value.code == 2


def test_codex_result_points_to_reviewed_commit_when_head_moves():
    commit = "1234abcd" * 5
    job = {
        "client": "codex",
        "kind": "review",
        "target": {"head": "HEAD", "head_commit": commit},
        "worktree": "/job/worktree",
        "repo_root": "/repo",
    }
    assert f"git show {commit}:<path>" in render.result_text(job, None)


def test_codex_approval_stop_lists_queued_commands_without_slash_commands():
    job = {"client": "codex", "id": "review-1", "status": "needs-approval", "queued_commands": ["pytest -q"]}
    text = render.next_steps(job)
    assert "`pytest -q`" in text and "Codex cannot grant" in text and "/agy:" not in text
    assert "recorded no commands" in render.next_steps(dict(job, queued_commands=[]))


def test_codex_missing_agy_points_to_the_codex_setup(monkeypatch, capsys):
    monkeypatch.setattr(agy_companion.agybin, "find_agy", lambda: None)
    assert agy_companion.main(["review"], codex=True) != 0
    err = capsys.readouterr().err
    assert "agy_codex.py setup" in err and "/agy:" not in err


def test_codex_review_round_trip(contained):
    env = dict(contained.env, AGY_COMPANION_SESSION_ID="claude-session", FAKE_AGY_SCENARIO="review")

    def run(*args):
        return subprocess.run(
            [sys.executable, str(SCRIPTS / "agy_codex.py"), *args],
            cwd=contained.repo,
            env=env,
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        ).stdout

    launch = run("review", "--background")
    job_id = launch.split()[2]
    assert f"agy_codex.py result {job_id}" in launch
    result = run("result", job_id, "--wait-seconds", "45")
    assert "Verdict:" in result and "/agy:" not in result
    assert "Status: completed" in run("status", job_id)
    assert contained.jobs() == []
    (record,) = (Path(env["HOME"]) / ".cache/agy-codex/state").glob("*/jobs/*/job.json")
    job = json.loads(record.read_text())
    assert job["id"] == job_id and job["session_id"] is None
    assert job["writable"] is False and job["isolation"] == "container"
