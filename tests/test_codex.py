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
    skills = list((plugin / manifest["skills"]).glob("*/SKILL.md"))
    assert {skill.parent.name for skill in skills} == {"agy-review", "agy-jobs"}
    for skill in skills:
        assert (skill.parent / "../../scripts/agy_codex.py").resolve().is_file()


@pytest.mark.parametrize("command", ["status", "result", "cancel", "apply", "resume"])
@pytest.mark.parametrize("reference", [[], ["task-2"], ["latest"]])
def test_codex_requires_full_job_ids(command, reference):
    args = [command, *reference]
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
    assert "`pytest -q`" in text and "agy_codex.py resume review-1" in text and "/agy:" not in text
    assert "recorded no commands" in render.next_steps(dict(job, queued_commands=[]))


def test_codex_missing_agy_points_to_the_codex_setup(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    monkeypatch.setattr(agy_companion.agybin, "find_agy", lambda: None)
    assert agy_companion.main(["review", "--isolation", "host"], codex=True) != 0
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


def test_codex_read_dirs_respect_configured_claude_paths(contained, tmp_path):
    inherited = tmp_path / "legacy" / "state"
    plugin_data = tmp_path / "plugin-data"
    config = tmp_path / "profile"
    public = config / "shared"
    public.mkdir(parents=True)
    env = dict(
        contained.env,
        AGY_COMPANION_STATE_DIR=str(inherited),
        CLAUDE_PLUGIN_DATA=str(plugin_data),
        CLAUDE_CONFIG_DIR=str(config),
        FAKE_AGY_SCENARIO="review",
    )

    def review(directory):
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "agy_codex.py"),
                "review",
                "--isolation",
                "container",
                "--read-dir",
                str(directory),
            ],
            cwd=contained.repo,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )

    for directory in (inherited.parent, plugin_data, config / "plugins" / "data" / "child"):
        directory.mkdir(parents=True, exist_ok=True)
        result = review(directory)
        assert result.returncode == 2 and "--read-dir refused" in result.stderr
    result = review(public)
    assert result.returncode == 0 and "Verdict:" in result.stdout


def codex(companion, *args, scenario="reply", check=True):
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "agy_codex.py"), *args],
        cwd=companion.repo,
        env=dict(companion.env, FAKE_AGY_SCENARIO=scenario),
        capture_output=True,
        text=True,
        check=check,
        timeout=60,
    )


def test_codex_approval_resume_keeps_then_removes_review_worktree(companion, tmp_path):
    out = codex(companion, "review", "--isolation", "host", "--no-os-sandbox", scenario="task").stdout
    job_id = out.split()[1]
    assert f"agy_codex.py resume {job_id}" in out
    (record,) = (Path(companion.env["HOME"]) / ".cache/agy-codex/state").glob("*/jobs/*/job.json")
    job = json.loads(record.read_text())
    assert job["status"] == "needs-approval"
    assert Path(job["worktree"]).is_dir()
    results = tmp_path / "empty result"
    results.touch()
    out = codex(companion, "resume", job_id, str(results), "--note", "exit 0", scenario="task").stdout
    assert "Tests pass" in out
    assert not Path(job["worktree"]).exists()


def test_codex_selects_concurrent_jobs_by_id(companion):
    flags = ("--isolation", "host", "--no-os-sandbox")
    writing = codex(companion, "task", *flags, "--read-only", "--background", "draft", scenario="stall").stdout.split()[
        2
    ]
    try:
        plan = codex(companion, "plan", *flags, "plan it", scenario="plan").stdout
        assert "# Plan" in plan
        assert "Status: completed" in codex(companion, "status", plan.split()[1]).stdout
        assert "still running" in codex(companion, "result", writing).stdout
    finally:
        codex(companion, "cancel", writing)
    assert "Status: cancelled" in codex(companion, "status", writing).stdout


def test_codex_container_rescue_applies_only_on_request(contained):
    source = contained.repo / "stats.py"
    before = source.read_bytes()
    out = codex(contained, "task", "fix it", scenario="container").stdout
    job_id = out.split()[1]
    assert f"agy_codex.py apply {job_id}" in out
    codex(contained, "task", "--resume-job", job_id, "--read-only", "inspect", scenario="reply")
    codex(contained, "apply", job_id, "--check")
    assert source.read_bytes() == before
    codex(contained, "apply", job_id)
    assert source.read_text() == "fixed\n"


def test_codex_writing_followup_stays_read_only(companion):
    out = codex(companion, "task", "--no-os-sandbox", "--read-only", "draft it", scenario="task").stdout
    job_id = out.split()[1]
    assert "agy_codex.py apply" not in out
    assert codex(companion, "task", "--resume-job", job_id, "edit", check=False).returncode == 2
    out = codex(companion, "resume", job_id, "--note", "skip the command").stdout
    assert "completed" in out
    assert "commands you requested were not run" in companion.agy_calls()[-1]["stdin"]
    assert codex(companion, "apply", job_id, check=False).returncode == 2
