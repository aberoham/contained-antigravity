"""Container isolation: job setup, the docker command, the in-container export, and the fallback.

The fake docker runs containers as host processes, so these tests cover the
plugin's side of the boundary. test_real_container.py runs the same jobs in a
real container when Docker is reachable.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import signal
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from agylib import container
from conftest import git


def _run_options(call: list[str]) -> list[str]:
    return [arg for arg in call[1:] if arg.startswith("-")]


def test_without_docker_jobs_fall_back_to_host_mode(companion):
    companion.run("review", "--no-os-sandbox", scenario="review")
    job = companion.job()
    assert job["isolation"] == "host"
    assert (Path(job["worktree"]).parent.parent / "jobs" / job["id"] / "policy.json").exists()
    assert companion.docker_runs() == []
    (call,) = companion.agy_calls()
    assert "--dangerously-skip-permissions" not in call["argv"]


def test_forcing_container_without_docker_is_an_error(companion):
    proc = companion.run("review", "--isolation", "container", scenario="review", check=False)
    assert proc.returncode == 17
    assert "not available" in proc.stderr
    assert companion.jobs() == []


def test_setup_reports_the_container_state(companion, contained):
    report = json.loads(companion.run("setup", "--json").stdout)
    assert report["docker_reachable"] is False and report["isolation"] == "host"
    assert "Docker daemon" in report["container_unavailable"]
    report = json.loads(contained.run("setup", "--json").stdout)
    assert report["docker_reachable"] and report["image_present"] and report["state_mountable"]
    assert report["container_signed_in"] is True
    assert report["isolation"] == "container" and report["container_unavailable"] is None


@pytest.mark.parametrize("version", ["1.5.9", "unknown"])
def test_jobs_check_the_version_of_the_selected_runtime(contained, version):
    env = {"FAKE_IMAGE_VERSION": version}
    report = json.loads(contained.run("setup", "--json", **env).stdout)
    assert report["version_ok"] and report["isolation"] == "host"
    assert "1.5.10" in report["container_unavailable"] and "build.sh" in report["container_unavailable"]
    refused = contained.run("review", "--isolation", "container", check=False, **env)
    assert refused.returncode == 17 and "1.5.10" in refused.stderr
    refused = contained.run("review", "--isolation", "host", check=False, FAKE_HOST_VERSION=version)
    assert refused.returncode == 13 and "agy update" in refused.stderr
    assert contained.jobs() == []


def test_container_setup_accepts_supported_image_with_older_host(contained):
    settings = Path(contained.env["HOME"]) / ".gemini" / "antigravity-cli" / "settings.json"
    settings.write_text('{"gcp": {"project": "p"}}')
    report = json.loads(contained.run("setup", "--json", FAKE_HOST_VERSION="1.5.9").stdout)
    assert report["isolation"] == "container" and report["ready"]
    assert not report["version_ok"] and report["image_agy_version"] == "1.5.15"


def test_container_resume_reports_unreachable_daemon_and_preserves_job(contained):
    contained.run("task", "--isolation", "container", "fix the bug", scenario="container")
    original = contained.job()
    refused = contained.run("task", "--resume-last", "continue", check=False, FAKE_DOCKER_MODE="absent")
    assert refused.returncode == 17 and "Docker daemon" in refused.stderr
    assert contained.job() == original


@pytest.mark.parametrize("isolation,key", [("host", "FAKE_HOST_VERSION"), ("container", "FAKE_IMAGE_VERSION")])
def test_resume_checks_runtime_before_changing_job_state(contained, isolation, key):
    contained.run("task", "--no-os-sandbox", "--isolation", isolation, "fix the bug", scenario="task")
    original = contained.job()
    refused = contained.run("task", "--resume-last", "continue", check=False, **{key: "1.5.9"})
    assert refused.returncode in (13, 17) and "1.5.9" in refused.stderr
    assert contained.job() == original


def test_an_image_built_for_another_user_is_refused_with_the_rebuild_command(contained):
    other = f"{os.getuid() + 1}:{os.getgid() + 1}"
    proc = contained.run(
        "review", "--isolation", "container", scenario="review", check=False, FAKE_DOCKER_IMAGE_USER=other
    )
    assert proc.returncode == 17
    assert f"built for user {other}" in proc.stderr and "containers/build.sh" in proc.stderr
    report = json.loads(contained.run("setup", "--json", FAKE_DOCKER_IMAGE_USER=other).stdout)
    assert report["image_user_matches"] is False and report["isolation"] == "host"
    assert f"built for user {other}" in report["container_unavailable"]
    # A named user cannot be checked and passes; a failed inspection does not.
    assert json.loads(contained.run("setup", "--json", FAKE_DOCKER_IMAGE_USER="agent").stdout)["image_user_matches"]
    from agylib import container

    assert container.image_user_matches(None) is False
    assert container.image_user_matches("") is True


def test_review_runs_in_a_container_with_only_the_job_paths_mounted(contained, repo):
    proc = contained.run("review", "--model", "low", scenario="review")
    assert "Verdict: needs-attention" in proc.stdout
    job = contained.job()
    assert job["isolation"] == "container" and job["os_sandbox"] is False
    worktree = Path(job["worktree"])
    state = Path(contained.env["AGY_COMPANION_STATE_DIR"])
    signin = Path(contained.env["AGY_CONTAINER_HOME"])
    job_mount = worktree.parent.parent / "jobs" / job["id"] / "container"
    version, probe, run = contained.docker_runs()
    assert version[-1] == "--version" and f"--user={os.getuid()}:{os.getgid()}" in version
    assert "touch" in probe
    assert [o for o in probe if o.startswith("--volume=")] == [f"--volume={state}:{state}"]
    options = _run_options(run)
    volumes = [o[len("--volume=") :] for o in options if o.startswith("--volume=")]
    # Exactly these, and nothing else: the job record, the result and the
    # sign-in home stay outside.
    assert volumes == [
        f"{worktree}:{worktree}:ro",  # a review never edits the copy
        f"{job_mount}:{job_mount}",
        f"{job['plugin_root']}:{job['plugin_root']}:ro",
    ]
    assert not any(str(repo) in v or str(signin) in v for v in volumes)
    log_file = Path(run[run.index("--log-file") + 1])
    assert log_file.parent == job_mount and log_file.name.startswith("agy-1-")
    assert "--init" in options and f"--env=HOME={job_mount / 'home'}" in options
    seeded = job_mount / "home" / ".gemini" / "antigravity-cli"
    assert (seeded / "antigravity-oauth-token").read_text() == '{"token": "fake"}'
    assert (seeded / "settings.json").read_text() == '{"enableTerminalSandbox": false}\n'
    assert all(oct((seeded / n).stat().st_mode)[-3:] == "600" for n in ("antigravity-oauth-token", "settings.json"))
    env = dict(o[len("--env=") :].split("=", 1) for o in options if o.startswith("--env="))
    keys = {env[f"GIT_CONFIG_KEY_{i}"]: env[f"GIT_CONFIG_VALUE_{i}"] for i in range(int(env["GIT_CONFIG_COUNT"]))}
    assert keys == {
        "safe.directory": "*",
        "user.name": "agy plugin",
        "user.email": "agy-plugin@localhost",
        "commit.gpgsign": "false",
    }
    assert any(o.startswith("--name=agy-review-") for o in options)
    assert run[-1] == "--dangerously-skip-permissions"
    (call,) = contained.agy_calls()
    assert call["argv"][-1] == "--dangerously-skip-permissions"
    assert "--json-schema" in call["argv"]
    assert "read-only shell commands" in json.loads(call["stdin"])["message"]["content"]
    assert not worktree.exists()


def test_container_task_keeps_repository_hooks_and_exports_from_inside(contained, repo, tmp_path):
    marker = tmp_path / "hook-ran"
    hooks = {
        "repo": {
            "PreToolUse": [
                {
                    "matcher": ".*",
                    "hooks": [{"type": "command", "command": f"touch {marker}; touch hook-marker", "timeout": 5}],
                }
            ]
        }
    }
    (repo / ".agents").mkdir()
    (repo / ".agents" / "hooks.json").write_text(json.dumps(hooks))
    git(repo, "add", ".agents")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "hooks")
    proc = contained.run("task", "fix the bug", scenario="container")
    job = contained.job()
    assert job["status"] == "completed" and job["isolation"] == "container"
    assert "Fixed stats.py" in proc.stdout
    worktree = Path(job["worktree"])
    assert (worktree / ".agents" / "hooks.json").exists()  # not emptied in container mode
    assert (worktree / ".git").is_dir()  # a self-contained copy, not a linked worktree
    assert str(worktree) not in git(repo, "worktree", "list")
    assert marker.exists()  # the fake docker runs the hook on the host; a real container keeps it inside
    message = json.loads(contained.agy_calls()[0]["stdin"])["message"]["content"]
    assert "inside a container" in message and "queued" not in message
    # The export ran as a second container, and the host read its output.
    export = contained.docker_runs()[-1]
    assert "_export" in export and "--entrypoint" in export
    assert f"--name=agy-{job['id']}-1-export" in export and "--init" in export
    assert [o for o in export if o.startswith("--volume=")] == [
        o for o in contained.docker_runs()[-2] if o.startswith("--volume=")
    ]
    assert export[-1] == str(worktree.parent.parent / "jobs" / job["id"] / "container")
    calls = [json.loads(line) for line in contained.docker_calls.read_text().splitlines()]
    assert ["rm", "--force", f"agy-{job['id']}-1-export"] in calls
    assert job["container"] is None
    stored = json.loads((Path(job["worktree"]).parent.parent / "jobs" / job["id"] / "result.json").read_text())
    assert stored["changed_paths"] == [["A", "hook-marker"], ["M", "stats.py"]]
    proc = contained.run("apply", job["id"])
    assert "Applied" in proc.stdout
    assert (repo / "stats.py").read_text() == "fixed\n"
    assert (repo / "hook-marker").exists()


def test_container_task_resumes_in_the_same_copy_and_conversation(contained, repo):
    contained.run("task", "fix the bug", scenario="container")
    first = contained.job()
    contained.run("task", "--resume-last", "--read-only", "carry on", scenario="container")
    job = contained.job()
    assert job["id"] == first["id"] and job["status"] == "completed"
    assert [r["kind"] for r in job["rounds"]] == ["initial", "follow-up"]
    calls = contained.agy_calls()
    assert calls[1]["argv"][calls[1]["argv"].index("--conversation") + 1] == job["conversation_id"]
    assert "mounted read-only" in json.loads(calls[1]["stdin"])["message"]["content"]
    worktree = job["worktree"]
    runs = [c for c in contained.docker_runs() if any(o.startswith("--name=agy-") for o in c)]
    names = [o for c in runs for o in c if o.startswith("--name=")]
    assert names == [
        f"--name=agy-{job['id']}-1",
        f"--name=agy-{job['id']}-1-export",
        f"--name=agy-{job['id']}-2",
        f"--name=agy-{job['id']}-2-export",
    ]
    assert f"--volume={worktree}:{worktree}" in runs[0]  # the task round edits
    assert f"--volume={worktree}:{worktree}:ro" in runs[2]  # the read-only round does not


def test_a_resumed_round_keeps_the_conversation_and_refreshes_the_sign_in(contained, repo):
    contained.run("task", "fix the bug", scenario="container")
    job = contained.job()
    home = Path(job["worktree"]).parent.parent / "jobs" / job["id"] / "container" / "home"
    cli = home / ".gemini" / "antigravity-cli"
    assert (cli / "conversations" / f"{job['conversation_id']}.db").is_file()
    (cli / "settings.json").write_text('{"hooks": "changed by a round"}')
    signin = Path(contained.env["AGY_CONTAINER_HOME"]) / ".gemini" / "antigravity-cli"
    (signin / "antigravity-oauth-token").write_text('{"token": "fresh"}')  # a new sign-in since round 1
    (signin / "settings.json").write_text('{"enableTerminalSandbox": false, "gcp": {"project": "p"}}\n')
    proc = contained.run("task", "--resume-last", "carry on", scenario="container")
    job = contained.job()
    assert job["status"] == "completed", proc.stdout
    assert "Continued" in proc.stdout  # the fake refuses to resume without its conversation file
    assert (cli / "settings.json").read_text() == '{"enableTerminalSandbox": false, "gcp": {"project": "p"}}\n'
    assert (cli / "antigravity-oauth-token").read_text() == '{"token": "fresh"}'


def test_seeding_never_writes_through_a_link_a_round_planted(contained, repo, tmp_path):
    contained.run("task", "fix the bug", scenario="container")
    job = contained.job()
    home = Path(job["worktree"]).parent.parent / "jobs" / job["id"] / "container" / "home"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "settings.json").write_text("precious\n")
    cli = home / ".gemini" / "antigravity-cli"
    shutil.rmtree(cli)
    os.symlink(outside, cli)  # what a round could do to the agent-writable home
    proc = contained.run("task", "--resume-last", "carry on", scenario="container", check=False)
    assert contained.job()["status"] == "failed"
    assert "not a plain directory" in (contained.job().get("error") or "") + proc.stdout
    assert (outside / "settings.json").read_text() == "precious\n"
    assert not (outside / "antigravity-oauth-token").exists()


def test_a_new_job_never_sees_another_jobs_home(contained, repo):
    contained.run("task", "fix the bug", scenario="container")
    first = contained.job()
    contained.run("task", "fix it again", scenario="container")
    jobs = {j["id"]: j for j in contained.jobs()}
    assert len(jobs) == 2
    homes = {Path(j["worktree"]).parent.parent / "jobs" / j["id"] / "container" / "home" for j in jobs.values()}
    assert len(homes) == 2 and first["id"] in jobs


def test_the_export_runs_with_an_empty_home_and_no_git_configuration(contained, repo):
    contained.run("task", "fix the bug", scenario="container")
    job = contained.job()
    export = contained.docker_runs()[-1]
    job_mount = Path(job["worktree"]).parent.parent / "jobs" / job["id"] / "container"
    assert f"--env=HOME={job_mount / 'export-home'}" in export
    assert "--env=GIT_CONFIG_GLOBAL=/dev/null" in export and "--env=GIT_CONFIG_NOSYSTEM=1" in export
    assert export[export.index("--entrypoint") + 1 : export.index("--entrypoint") + 2] == ["python3"]
    assert export[export.index(container.DEFAULT_IMAGE) + 1] == "-I"
    assert (job_mount / "export-home").is_dir() and not any((job_mount / "export-home").iterdir())


def test_read_dirs_overlapping_the_sign_in_or_state_are_refused(contained, tmp_path):
    signin = contained.env["AGY_CONTAINER_HOME"]
    for target in (signin, f"{signin}/.gemini", contained.env["AGY_COMPANION_STATE_DIR"], str(tmp_path), "/"):
        proc = contained.run("review", "--read-dir", target, scenario="review", check=False)
        assert proc.returncode == 2 and "--read-dir refused" in proc.stderr, target
    assert contained.jobs() == []
    unrelated = tmp_path.parent / f"{tmp_path.name}-docs"
    unrelated.mkdir()
    proc = contained.run("review", "--read-dir", str(unrelated), scenario="review")
    assert "Verdict" in proc.stdout
    assert f"--volume={unrelated}:{unrelated}:ro" in contained.docker_runs()[-1]


def test_plans_are_read_from_the_job_home(contained):
    proc = contained.run("plan", "add a median function", scenario="plan")
    assert "1. Do the thing." in proc.stdout
    job = contained.job()
    assert job["status"] == "completed"
    assert "/container/home/.gemini/antigravity-cli/brain/" in proc.stdout


def test_cancel_removes_the_container(contained):
    contained.run("task", "--background", "--model", "high", "--stall-seconds", "600", "do it", scenario="stall")
    job = contained.wait_for(lambda j: j["status"] == "running" and j.get("container"))
    assert job["container"] == f"agy-{job['id']}-1"
    contained.run("cancel")
    assert contained.job()["status"] == "cancelled"
    calls = [json.loads(line) for line in contained.docker_calls.read_text().splitlines()]
    assert ["rm", "--force", job["container"]] in calls


def test_apply_uses_the_baseline_snapshot_not_git_in_the_copy(contained, repo):
    contained.run("task", "fix the bug", scenario="container")
    job = contained.job()
    job_dir = Path(job["worktree"]).parent.parent / "jobs" / job["id"]
    tree = json.loads((job_dir / "baseline-tree.json").read_text())
    assert set(tree) == {"stats.py", "notes.txt"}
    (repo / "stats.py").write_text("someone else changed this\n")
    proc = contained.run("apply", check=False)
    assert proc.returncode == 2 and "stats.py" in proc.stderr


def test_wrap_puts_every_mount_at_its_host_path(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))  # keeps the developer's Keychain sign-in out of the job
    monkeypatch.setenv("AGY_COMPANION_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("AGY_CONTAINER_HOME", str(tmp_path / "container-home"))
    monkeypatch.setenv("PATH", str(tmp_path))
    (tmp_path / "docker").write_text("")
    (tmp_path / "docker").chmod(0o755)
    (tmp_path / "container-home" / ".gemini" / "antigravity-cli").mkdir(parents=True)
    (tmp_path / "job").mkdir()
    container.seed_home(tmp_path / "job")
    mounts = container.Mounts(tmp_path / "wt", tmp_path / "job", tmp_path / "plugin", [tmp_path / "docs"], True)
    command = container.wrap(["agy", "--model", "x", "--print="], name="agy-job-1", mounts=mounts)
    assert command[0] == str(tmp_path / "docker")
    assert command[1] == "run" and "--rm" in command[:5] and "--interactive" in command[:5]
    assert f"--volume={tmp_path / 'docs'}:{tmp_path / 'docs'}:ro" in command
    assert f"--volume={tmp_path / 'job' / 'container'}:{tmp_path / 'job' / 'container'}" in command
    assert not any(o == f"--volume={tmp_path / 'job'}:{tmp_path / 'job'}" for o in command)
    assert not any(str(tmp_path / "container-home") in o for o in command)
    assert f"--env=HOME={tmp_path / 'job' / 'container' / 'home'}" in command
    assert command[-4:] == ["--model", "x", "--print=", "--dangerously-skip-permissions"]
    assert "agy" not in command[command.index(container.DEFAULT_IMAGE) + 1 :]


def test_apply_refuses_a_job_without_a_baseline_snapshot(contained, repo):
    contained.run("task", "fix the bug", scenario="container")
    job = contained.job()
    (Path(job["worktree"]).parent.parent / "jobs" / job["id"] / "baseline-tree.json").unlink()
    proc = contained.run("apply", check=False)
    assert proc.returncode == 2 and "no baseline snapshot" in proc.stderr
    assert "sum(v[1:])" in (repo / "stats.py").read_text()


def _signins(tmp_path, monkeypatch) -> tuple[Path, Path]:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AGY_CONTAINER_HOME", str(tmp_path / "container-home"))
    host = tmp_path / "home" / ".gemini" / "antigravity-cli"
    signin = tmp_path / "container-home" / ".gemini" / "antigravity-cli"
    host.mkdir(parents=True)
    signin.mkdir(parents=True)
    return host, signin


def _token(directory: Path, name: str, mtime: float) -> None:
    path = directory / "antigravity-oauth-token"
    path.write_text(json.dumps({"token": name}))
    os.utime(path, (mtime, mtime))


def test_jobs_copy_the_host_sign_in_with_only_its_cloud_project(tmp_path, monkeypatch):
    host, signin = _signins(tmp_path, monkeypatch)
    _token(host, "host", 2000)
    _token(signin, "container", 1000)
    (host / "settings.json").write_text(
        json.dumps({"gcp": {"project": "p", "location": "eu"}, "permissions": {"allow": ["command(*)"]}, "hooks": {}})
    )
    (tmp_path / "job").mkdir()
    cli = container.seed_home(tmp_path / "job") / ".gemini" / "antigravity-cli"
    assert container.signin_source() == ("host", host)
    assert json.loads((cli / "antigravity-oauth-token").read_text()) == {"token": "host"}
    assert json.loads((cli / "settings.json").read_text()) == {
        "enableTerminalSandbox": False,
        "gcp": {"project": "p", "location": "eu"},
    }


def test_a_newer_container_sign_in_wins_and_keeps_its_own_settings(tmp_path, monkeypatch):
    host, signin = _signins(tmp_path, monkeypatch)
    _token(host, "host", 1000)
    _token(signin, "container", 2000)
    (signin / "settings.json").write_text('{"enableTerminalSandbox": false, "gcp": {"project": "c"}}\n')
    (tmp_path / "job").mkdir()
    cli = container.seed_home(tmp_path / "job") / ".gemini" / "antigravity-cli"
    assert json.loads((cli / "antigravity-oauth-token").read_text()) == {"token": "container"}
    assert (cli / "settings.json").read_text() == '{"enableTerminalSandbox": false, "gcp": {"project": "c"}}\n'


def test_a_half_written_or_empty_token_is_never_copied(tmp_path, monkeypatch):
    host, signin = _signins(tmp_path, monkeypatch)
    (host / "antigravity-oauth-token").write_text('{"token": "ho')  # agy is rewriting it
    os.utime(host / "antigravity-oauth-token", (3000, 3000))
    assert container.signin_source() is None
    _token(signin, "container", 1000)
    assert container.signin_source() == ("container", signin)
    (signin / "antigravity-oauth-token").write_text("{}")
    assert container.signin_source() is None


def test_read_dirs_may_not_expose_the_host_sign_in(tmp_path, monkeypatch):
    host, _ = _signins(tmp_path, monkeypatch)
    assert "host's agy sign-in" in (container.overlaps_protected(tmp_path / "home" / ".gemini") or "")
    assert container.overlaps_protected(tmp_path / "elsewhere") is None


def test_read_dirs_may_not_expose_codex_job_state(tmp_path, monkeypatch):
    _signins(tmp_path, monkeypatch)
    assert "Codex job state" in (container.overlaps_protected(tmp_path / "home" / ".cache") or "")


def test_a_job_gets_the_token_bytes_that_were_checked_even_if_the_file_changes(tmp_path, monkeypatch):
    host, _ = _signins(tmp_path, monkeypatch)
    _token(host, "checked", 2000)
    real_read_bytes = Path.read_bytes

    def read_then_rewrite(path: Path) -> bytes:
        data = real_read_bytes(path)
        if path.name == "antigravity-oauth-token":
            path.write_text('{"token":')  # agy starts rewriting it straight after the check
        return data

    monkeypatch.setattr(Path, "read_bytes", read_then_rewrite)
    (tmp_path / "job").mkdir()
    cli = container.seed_home(tmp_path / "job") / ".gemini" / "antigravity-cli"
    assert json.loads(real_read_bytes(cli / "antigravity-oauth-token")) == {"token": "checked"}


def _keychain(monkeypatch, home: Path, stored: str | None, mdat: str = "20261124033118") -> list[list[str]]:
    """Make the host look like a Mac whose agy keeps `stored` in the Keychain; return the commands run.

    `stored` None means the item does not exist. Only /usr/bin/security is
    faked; any other command would be a test bug, so it fails loudly.
    """
    calls: list[list[str]] = []

    def run(command, **kwargs):
        assert command[0] == container.KEYCHAIN_TOOL, command
        calls.append(command)
        if stored is None:
            return subprocess.CompletedProcess(command, 44, "", "The specified item could not be found.\n")
        if command[-1] == "-w":
            return subprocess.CompletedProcess(command, 0, stored + "\n", "")
        return subprocess.CompletedProcess(command, 0, f'    "mdat"<timedate>=0x00  "{mdat}Z\\000"\n', "")

    monkeypatch.setattr(container.sys, "platform", "darwin")
    monkeypatch.setattr(container.pwd, "getpwuid", lambda uid: SimpleNamespace(pw_dir=str(home)))
    monkeypatch.setattr(container.subprocess, "run", run)
    return calls


def _keyring(value: dict) -> str:
    return container.KEYRING_PREFIX + base64.b64encode(json.dumps(value).encode()).decode()


def test_jobs_copy_a_keychain_sign_in_when_the_token_file_has_none(tmp_path, monkeypatch):
    host, _ = _signins(tmp_path, monkeypatch)
    (host / "antigravity-oauth-token").write_text('{"token": null, "saved_wif_provider": "p"}')
    (host / "settings.json").write_text(json.dumps({"gcp": {"project": "p", "location": "eu"}}))
    signin = {"token": {"refresh_token": "r"}, "auth_method": "gcp"}
    _keychain(monkeypatch, tmp_path / "home", _keyring(signin))
    (tmp_path / "job").mkdir()
    cli = container.seed_home(tmp_path / "job") / ".gemini" / "antigravity-cli"
    assert container.signin_source() == ("host", host)
    assert json.loads((cli / "antigravity-oauth-token").read_text()) == signin
    assert json.loads((cli / "settings.json").read_text())["gcp"] == {"project": "p", "location": "eu"}


def test_the_newest_of_token_file_keychain_and_container_sign_in_wins(tmp_path, monkeypatch):
    host, signin = _signins(tmp_path, monkeypatch)
    keychain_time = datetime(2026, 11, 24, 3, 31, 18, tzinfo=timezone.utc).timestamp()
    _keychain(monkeypatch, tmp_path / "home", _keyring({"token": "keychain"}))
    _token(host, "file", keychain_time - 60)
    _token(signin, "container", keychain_time - 30)
    (tmp_path / "job").mkdir()
    cli = container.seed_home(tmp_path / "job") / ".gemini" / "antigravity-cli"
    assert json.loads((cli / "antigravity-oauth-token").read_text()) == {"token": "keychain"}
    _token(signin, "container", keychain_time + 30)
    assert container.signin_source() == ("container", signin)


def test_an_unusable_keychain_item_gives_no_sign_in(tmp_path, monkeypatch):
    host, _ = _signins(tmp_path, monkeypatch)
    unusable = (
        None,
        container.KEYRING_PREFIX + "not base64!",
        container.KEYRING_PREFIX + "é",
        _keyring({"token": None}),
        "not json",
        "[" * 100_000,
    )
    for stored in unusable:
        _keychain(monkeypatch, tmp_path / "home", stored)
        assert container.signin_source() is None, stored[:20] if stored else stored


def test_a_keychain_that_cannot_be_asked_gives_no_sign_in(tmp_path, monkeypatch):
    _signins(tmp_path, monkeypatch)
    calls = _keychain(monkeypatch, tmp_path / "home", _keyring({"token": "keychain"}))

    def times_out(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(container.subprocess, "run", times_out)
    assert container.signin_source() is None
    monkeypatch.setattr(container.sys, "platform", "linux")
    monkeypatch.setattr(container.subprocess, "run", lambda command, **kwargs: calls.append(command))
    assert container.signin_source() is None
    monkeypatch.setattr(container.sys, "platform", "darwin")

    def no_such_user(uid):
        raise KeyError(uid)

    monkeypatch.setattr(container.pwd, "getpwuid", no_such_user)
    assert container.signin_source() is None
    assert calls == []


def test_the_keychain_is_not_read_when_home_is_not_the_users_own(tmp_path, monkeypatch):
    _signins(tmp_path, monkeypatch)
    calls = _keychain(monkeypatch, tmp_path / "someone-else", _keyring({"token": "keychain"}))
    assert container.signin_source() is None
    assert calls == []


def test_a_keychain_item_without_a_valid_modification_time_loses_to_any_file(tmp_path, monkeypatch):
    for mdat in ("", "20261399999999"):
        host, _ = _signins(tmp_path / mdat, monkeypatch)
        _keychain(monkeypatch, tmp_path / mdat / "home", _keyring({"token": "keychain"}), mdat=mdat)
        alone = container._newest_signin()
        assert alone is not None and json.loads(alone[2]) == {"token": "keychain"}
        _token(host, "file", 1)
        with_file = container._newest_signin()
        assert with_file is not None and json.loads(with_file[2]) == {"token": "file"}


def test_a_container_that_survives_removal_fails_the_job(contained):
    contained.run("review", "--isolation", "container", scenario="review", check=False, FAKE_DOCKER_RM_FAILS="1")
    job = contained.job()
    assert job["status"] == "failed"
    name = f"agy-{job['id']}-1"
    assert f"Docker did not remove the job's container {name}" in job["error"]
    assert f"docker rm --force {name}" in job["error"]
    assert job["failure"] == "container"
    assert job["container"] == name  # kept, so the next round or the job's deletion tries again
    assert job["rounds"][0]["usage"]["total_tokens"] == 120  # the turn's usage is still counted
    assert Path(job["worktree"]).is_dir()  # not deleted while the container may still use it


def test_an_export_that_fails_keeps_its_own_error_when_its_container_survives(contained):
    contained.run(
        "task",
        "--isolation",
        "container",
        "fix the bug",
        scenario="container",
        check=False,
        FAKE_DOCKER_EXPORT_FAILS="1",
        FAKE_DOCKER_RM_FAILS="-export",
    )
    job = contained.job()
    name = f"agy-{job['id']}-1-export"
    assert job["status"] == "failed" and job["failure"] == "agy-failed"
    assert "the export container failed: export: no space left on device" in job["error"]
    assert f"Also, Docker did not remove the job's container {name}" in job["error"]
    assert job["container"] == name


def test_a_turn_container_that_survives_removal_keeps_the_turns_own_failure(contained):
    contained.run("review", "--isolation", "container", scenario="licence", check=False, FAKE_DOCKER_RM_FAILS="1")
    job = contained.job()
    name = f"agy-{job['id']}-1"
    assert job["status"] == "failed" and job["failure"] == "licence"
    assert "licence" in job["error"]
    assert f"Also, Docker did not remove the job's container {name}" in job["error"]
    assert job["container"] == name


def test_an_export_container_that_survives_removal_is_reported_beside_agys_error(contained):
    contained.run(
        "task", "--isolation", "container", "do it", scenario="licence", check=False, FAKE_DOCKER_RM_FAILS="-export"
    )
    job = contained.job()
    assert job["status"] == "failed"
    name = f"agy-{job['id']}-1-export"
    assert job["failure"] == "licence" and "licence" in job["error"]
    assert f"Also, could not export agy's changes: Docker did not remove the job's container {name}" in job["error"]
    assert job["container"] == name


def test_remove_trusts_a_successful_rm_and_asks_docker_otherwise(monkeypatch):
    import subprocess

    from agylib import container

    asked = []

    def answers(rm, ps):
        def fake(*args, timeout):
            asked.append(args[0])
            reply = rm if args[0] == "rm" else ps
            return None if reply is None else subprocess.CompletedProcess(args, reply[0], reply[1], "")

        return fake

    cases = [
        ((0, ""), None, True),  # removed; a slow `docker ps` must not undo that
        (None, (0, ""), True),  # rm timed out, but the container is gone
        ((1, ""), (0, "0123456789ab\n"), False),  # still listed
        ((1, ""), None, False),  # Docker could not say
    ]
    for rm, ps, gone in cases:
        asked.clear()
        monkeypatch.setattr(container, "_docker", answers(rm, ps))
        assert container.remove("agy-x-1") is gone, (rm, ps)
        assert asked == (["rm"] if rm == (0, "") else ["rm", "ps"])


def test_a_container_left_by_one_round_is_removed_before_the_next(contained):
    contained.run("task", "fix the bug", scenario="container", check=False, FAKE_DOCKER_RM_FAILS="1")
    first = contained.job()
    left = f"agy-{first['id']}-1"
    assert first["status"] == "failed" and first["container"] == left
    agy_runs = len(contained.agy_calls())

    # While Docker still cannot remove it, the next round stops before agy starts.
    contained.run("task", "--resume-last", "carry on", scenario="container", check=False, FAKE_DOCKER_RM_FAILS=left)
    job = contained.job()
    assert job["status"] == "failed" and job["failure"] == "container"
    assert f"Docker did not remove the job's container {left}" in job["error"]
    assert len(contained.agy_calls()) == agy_runs

    # Once it can, the round removes it first and runs.
    contained.run("task", "--resume-last", "carry on", scenario="container")
    job = contained.job()
    assert job["status"] == "completed" and job["container"] is None
    calls = [json.loads(line) for line in contained.docker_calls.read_text().splitlines()]
    assert calls.count(["rm", "--force", left]) >= 3


def test_a_round_that_fails_in_cleanup_hides_the_earlier_patch(contained, repo):
    contained.run("task", "fix the bug", scenario="container")
    job = contained.job()
    assert "apply cleanly" in contained.run("apply", job["id"], "--check").stdout
    second = f"agy-{job['id']}-2"
    contained.run("task", "--resume-last", "carry on", scenario="container", check=False, FAKE_DOCKER_RM_FAILS=second)
    job = contained.job()
    assert job["status"] == "failed" and "Docker did not remove" in job["error"]
    result = contained.run("result", job["id"], check=False)
    assert "Docker did not remove" in result.stdout + result.stderr
    assert "changed no files" in contained.run("apply", job["id"], "--check").stdout


def test_cleaning_up_a_job_tries_its_leftover_container_again(contained):
    contained.run("task", "fix the bug", scenario="container", check=False, FAKE_DOCKER_RM_FAILS="1")
    job = contained.job()
    before = contained.docker_calls.read_text().count(f'"agy-{job["id"]}-1"')
    # Docker still cannot remove it; the working copy goes anyway.
    assert "Removed 1 job working copy" in contained.run("cleanup", job["id"], FAKE_DOCKER_RM_FAILS="1").stdout
    assert contained.docker_calls.read_text().count(f'"agy-{job["id"]}-1"') > before
    assert not Path(job["worktree"]).exists()


def test_a_worker_stopped_during_the_export_still_removes_the_export_container(contained):
    contained.run("task", "--background", "fix the bug", scenario="container", FAKE_DOCKER_EXPORT_SLEEPS="1")
    job = contained.wait_for(lambda j: str(j.get("container") or "").endswith("-export"))
    name = job["container"]
    os.kill(job["pid"], signal.SIGTERM)  # what a cancel or session end sends the worker
    name_removed = ["rm", "--force", name]
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        calls = [json.loads(line) for line in contained.docker_calls.read_text().splitlines()]
        if name_removed in calls:
            break
        time.sleep(0.2)
    assert name_removed in calls


def test_codex_read_dirs_may_not_expose_claude_code_job_state(tmp_path, monkeypatch):
    _signins(tmp_path, monkeypatch)
    monkeypatch.setenv("AGY_COMPANION_STATE_DIR", str(container.codex_state_root()))
    claude_data = tmp_path / "home" / ".claude"
    assert "Claude Code's plugin data" in (container.overlaps_protected(claude_data) or "")
