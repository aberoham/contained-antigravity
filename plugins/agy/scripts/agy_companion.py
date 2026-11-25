#!/usr/bin/env python3
"""Local companion for Google's Antigravity CLI (agy).

Starts agy jobs in isolated git worktrees, tracks them, and reports results.
Standard library only; runs on the macOS system Python 3.9.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).resolve().parent
PLUGIN_ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

from agylib import agybin, container, failure, gitctx, render, sandbox  # noqa: E402
from agylib.runner import JobRunner, terminate_job_processes  # noqa: E402
from agylib.state import (  # noqa: E402
    ACTIVE_STATUSES,
    SESSION_ID_ENV,
    STATE_DIR_ENV,
    TERMINAL_STATUSES,
    JobStore,
    drop_finished_working_copy,
    new_job_id,
    now_iso,
    parse_iso,
    read_json,
    record_session_job,
    session_index,
    state_root,
    store_holding,
    write_json,
)

KIND_MODES = {"review": None, "adversarial-review": None, "plan": "plan", "task": "accept-edits"}
DEFAULT_WAIT_SECONDS = 100  # stays under Claude Code's default two-minute Bash timeout
ISOLATION_CHOICES = ("auto", "container", "host")
HOST_SHELL_RULE = (
    "Use your file tools (read_file, grep_search, find_by_name and list_dir) to inspect the "
    "repository before reaching for run_command. Shell commands that are not read-only are not "
    "run: they are queued for the colleague's assistant, which may run them and send you the "
    "output in a later turn."
)
CONTAINER_SHELL_RULE = (
    "You are inside a container that holds only this working copy, so run the commands you need, including tests. "
    "The copy is a single commit: it has no history, no remotes and no other branches or refs, and no path outside "
    "the repository (except any extra read directory named below) exists here."
)
CONTAINER_READ_ONLY_SHELL_RULE = (
    "You are inside a container with this working copy mounted read-only: inspect files with your "
    "file tools or read-only shell commands, and do not write to the working copy. The copy is a "
    "single commit: it has no history, no remotes and no other branches or refs, and no path "
    "outside the repository (except any extra read directory named below) exists here."
)
HOST_REVIEW_GUIDANCE = (
    "Use the material in <repository_context> as primary evidence. Do not use shell commands in this "
    "job; most are blocked. Use your file tools (read, list, find and search files) to inspect "
    "the repository."
)
CONTAINER_REVIEW_GUIDANCE = (
    "Use the material in <repository_context> as primary evidence, and inspect the working copy with "
    "your file tools or read-only shell commands. Do not change files. The working copy is a single "
    "commit with no history, remotes or other refs, so git log and git diff show nothing useful there: the "
    "change under review is the diff in <repository_context>, and the files in the working copy are its result."
)
HOST_PLAN_GUIDANCE = (
    "Do not use shell commands in this job; most are blocked. Use your file tools "
    "(read_file, list_dir, find_by_name and grep_search) to inspect the repository."
)
CONTAINER_PLAN_GUIDANCE = (
    "The working copy is mounted read-only as a single commit with no history, remotes or "
    "other refs. Inspect it with your file tools or read-only shell commands, and do not "
    "write inside the working copy."
)
WRITABLE_TASK_ROLE = (
    "You are working on a copy of a colleague's repository in a separate git working copy. "
    "Your file edits stay there until they review and apply them."
)
READ_ONLY_TASK_ROLE = (
    "You have read-only access to a copy of a colleague's repository. Do not edit or create "
    "any file: writes are blocked, so put your entire answer in your reply."
)
WRITABLE_WORKING_RULES = (
    "Keep changes narrow and inside the task. Do not refactor unrelated code. Read the relevant "
    "files before you change them, and quote code or text only from files you opened in this "
    "session. When you need a command output to continue, such as a test run, call run_command "
    "once. If the command is not run, continue with what you can do and, at the end of your "
    "reply, list each command you still need and why."
)
READ_ONLY_WORKING_RULES = (
    "Stay inside the task. Read any relevant files before making claims about them, quote code "
    "or text only from files you opened in this session, and separate observed facts from "
    "inferences. When you need a command output that is blocked, call run_command once, "
    "continue with what you can do, and list each command you still need and why at the end of "
    "your reply."
)
WRITABLE_FINAL_REPORT = (
    "End with: what you changed (file by file), what you verified and how, what you could not "
    "verify, and any commands you still need."
)
READ_ONLY_FINAL_REPORT = (
    "Give the answer or text the task asked for directly. When the task asks for diagnosis or "
    "code analysis rather than a standalone draft, end with what you verified, what you could "
    "not verify, and any commands you still need."
)
# What a task request may name that the job cannot reach. A container copy is
# one commit with no refs; in either mode only the repository and any
# --read-dir directory can be read.
MISSING_REF = re.compile(r"(?<![\w/-])(?:origin/[\w.-][\w./-]*|refs/[\w.-][\w./-]*|pr/\d+)")
ABSOLUTE_PATH = re.compile(r"(?<![\w.:/~-])/[\w.@+-]+(?:/[\w.@+-]+)+")
# Material pasted into a request, which is the remedy and not the problem: a
# fenced block, or the lines of a diff.
FENCED = re.compile(r"^(```|~~~).*?(?:^\1|\Z)", re.S | re.M)
HUNK_HEADER = re.compile(r"^@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@")
DIFF_HEADERS = (
    "diff --git ",
    "index ",
    "--- ",
    "+++ ",
    "new file mode ",
    "deleted file mode ",
    "old mode ",
    "new mode ",
    "similarity index ",
    "rename from ",
    "rename to ",
    "Binary files ",
)
CONTAINER_READ_ONLY_NOTE = (
    "\n\nThis round is read-only: the working copy is mounted read-only, so do not try to edit "
    "files or run commands that write there. Put your entire answer in your reply.\n"
)
HOST_READ_ONLY_NOTE = (
    "\n\nThis round is read-only: file writes are blocked by policy, so do not try to edit "
    "files or run commands that write. Put your entire answer in your reply.\n"
)


class UsageError(Exception):
    pass


class ClassifiedError(UsageError):
    """A refusal to start a job that has its own failure class and exit code."""

    def __init__(self, failure_class: str, message: str):
        super().__init__(message)
        self.failure = failure_class


def emit(value: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(value, indent=2))
    else:
        print(value)


PLACEHOLDER = re.compile(r"\{\{([A-Z_]+)\}\}")


def template(name: str, **values: str) -> str:
    text = (PLUGIN_ROOT / "prompts" / name).read_text(encoding="utf-8")
    if text.startswith("<!--"):  # licence notice for people, not for the model
        text = text[text.index("-->") + 3 :].lstrip("\n")
    return PLACEHOLDER.sub(lambda match: values.get(match.group(1), match.group(0)), text)


def _expand_path(raw: str, flag: str) -> Path:
    try:
        return Path(raw).expanduser()
    except RuntimeError as exc:
        raise UsageError(f"cannot resolve {flag} {raw}: {exc}") from exc


def _resolve_read_dirs(raw_dirs: list[str] | None, in_container: bool) -> list[str]:
    pairs = [(raw, _expand_path(raw, "--read-dir").resolve()) for raw in (raw_dirs or [])]
    if in_container:
        for _raw, directory in pairs:
            problem = container.overlaps_protected(directory)
            if problem:
                raise UsageError(f"--read-dir refused: {problem}.")
    for raw, directory in pairs:
        if not directory.is_dir():
            raise UsageError(f"--read-dir {raw} is not an existing directory.")
    return list(dict.fromkeys(str(directory) for _raw, directory in pairs))


# setup ------------------------------------------------------------------------


def setup_report() -> dict[str, Any]:
    binary = agybin.find_agy()
    version = agybin.agy_version(binary) if binary else None
    settings = agybin.read_settings()
    raw_gcp = settings.get("gcp")
    gcp: dict[str, Any] = raw_gcp if isinstance(raw_gcp, dict) else {}
    report: dict[str, Any] = {
        "agy_path": str(binary) if binary else None,
        "agy_version": ".".join(map(str, version)) if version else None,
        "version_ok": bool(version and version >= agybin.MIN_VERSION),
        "minimum_version": agybin.MIN_VERSION_TEXT,
        "gcp_project": gcp.get("project"),
        "gcp_location": gcp.get("location"),
        "permission_allow_rules": len((settings.get("permissions") or {}).get("allow") or []),
        "os_sandbox": sandbox.available(),
        "python": sys.version.split()[0],
    }
    signed_in, detail = agybin.sign_in_check(binary) if binary else (None, "agy not found")
    report["signed_in"] = signed_in
    report["sign_in_detail"] = detail
    report["token_file"] = agybin.token_present()
    report.update(container.report())
    report["container_unavailable"] = container.unavailable_reason(report)
    report["isolation"] = "host" if report["container_unavailable"] else "container"
    report["default_model"] = agybin.settings_model(model_settings(report["isolation"]))
    problems = []
    if not binary:
        problems.append("agy is not installed or not on PATH. Install it, or set AGY_BIN to its path.")
    elif reason := agybin.version_problem(version):
        problems.append(f"{reason}; run `agy update`.")
    if binary and signed_in is False:
        problems.append("agy is not signed in. Run `agy` once in a terminal and sign in.")
    elif binary and signed_in is None:
        problems.append(f"Could not confirm agy's sign-in: {detail.rstrip('.')}.")
    if binary and not report["gcp_project"]:
        problems.append("agy has no Google Cloud project set; choose one when you sign in.")
    report["ready"] = not problems
    report["problems"] = problems
    return report


def model_settings(isolation: str) -> dict[str, Any]:
    """The agy settings whose model a job falls back to: the sign-in a container job copies, else the host's."""
    source = container.signin_source() if isolation == "container" else None
    return agybin.read_settings(source[1] if source else None)


def sign_in_label(report: dict[str, Any]) -> str:
    if report["signed_in"] is None:
        return "unknown"
    if not report["signed_in"]:
        return "no"
    if report["token_file"]:
        return "yes (token file)"
    return "yes (not in the token file; agy keeps it elsewhere, such as the keychain)"


def cmd_setup(args: argparse.Namespace) -> int:
    report = setup_report()
    if args.json:
        emit(report, True)
        return 0
    lines = [f"agy: {report['agy_path'] or 'not found'} (version {report['agy_version'] or '-'})"]
    lines.append(f"Signed in: {sign_in_label(report)}")
    lines.append(f"Google Cloud project: {report['gcp_project'] or '-'} ({report['gcp_location'] or '-'})")
    lines.append(f"macOS write sandbox: {'available' if report['os_sandbox'] else 'not available'}")
    lines.append(f"Default model: {report['default_model'] or 'agy chooses'} (Claude callers map to their own)")
    lines.append(f"Isolation: {report['isolation']}")
    reason = report["container_unavailable"]
    if reason:
        lines.append(f"Container mode is not available: {reason}.")
    else:
        lines.append(f"Container: {report['image']} with sign-in under {report['signin_home']}")
    lines += [f"Problem: {p}" for p in report["problems"]] or ["Ready."]
    print("\n".join(lines))
    return 0


# starting jobs ------------------------------------------------------------------


def start_job(
    args: argparse.Namespace,
    kind: str,
    message: str,
    *,
    writable: bool,
    extra: dict[str, Any] | None = None,
    copy_files: dict[str, str] | None = None,
) -> int:
    """Create the job's working copy and record, start its worker, and wait or report.

    A review of another ref passes it as extra["target"]; the copy is then
    built from that ref's commit. copy_files are written into the copy
    after its baseline commit.
    """
    binary = agybin.find_agy()
    if not binary:
        setup = "agy_codex.py setup" if getattr(args, "codex", False) else "/agy:setup"
        raise ClassifiedError("agy-missing", f"agy is not installed or not on PATH. Run {setup}.")
    root = gitctx.repo_root(os.getcwd())
    store = JobStore(root)
    isolation = choose_isolation(args)
    if isolation == "host":
        require_host_version(binary)
    job_id = new_job_id(kind)
    job_dir = store.jobs / job_id
    worktree = store.dir / "worktrees" / job_id
    record: dict[str, Any] = {
        "id": job_id,
        "kind": kind,
        "status": "queued",
        "phase": "starting",
        "model": agybin.resolve_model(args.model, caller=args.caller_model, settings=model_settings(isolation)),
        "mode": None if (kind == "task" and not writable) else KIND_MODES[kind],
        "writable": writable,
        "worktree": str(worktree),
        "repo_root": str(root),
        "agy_bin": str(binary),
        "plugin_root": str(PLUGIN_ROOT),
        "session_id": os.environ.get(SESSION_ID_ENV),
        "isolation": isolation,
        "os_sandbox": False,
        "json_schema": str(PLUGIN_ROOT / "schemas" / "review-output.schema.json")
        if kind in ("review", "adversarial-review")
        else None,
        "next_round_kind": "initial",
        "read_dirs": _resolve_read_dirs(args.read_dir, isolation == "container"),
        "generation": 1,
    }
    head_commit = ((extra or {}).get("target") or {}).get("head_commit")
    if isolation == "container":
        record["baseline"] = gitctx.create_copy(root, worktree, head_commit)
    else:
        record["baseline"] = gitctx.create_worktree(root, worktree, hook_command(job_dir), head_commit)
        try:
            git_admin = Path(gitctx.git(worktree, "rev-parse", "--absolute-git-dir").strip())
        except Exception:
            gitctx.remove_worktree(root, worktree)
            raise
        record["os_sandbox"] = sandbox.available() and not args.no_os_sandbox
        record["writable_paths"] = [str(worktree), str(agybin.agy_home()), str(job_dir), str(git_admin)]
    try:
        gitctx.add_copy_files(worktree, copy_files or {})
        tree = gitctx.baseline_tree(worktree, record["baseline"])
    except Exception:
        gitctx.remove_worktree(root, worktree)
        raise
    if args.stall_seconds:
        record["stall_seconds"] = args.stall_seconds
    if getattr(args, "codex", False):
        record["client"] = "codex"
    record.update(extra or {})
    if record["read_dirs"]:
        message += "\n\nYou may also read files in: " + ", ".join(record["read_dirs"]) + "\n"
    store.create(record)
    write_json(job_dir / "baseline-tree.json", tree)
    if isolation == "host":
        write_json(job_dir / "policy.json", policy_for(record))
    record_session_job(record["session_id"], root, job_id)
    (job_dir / "next-message.md").write_text(message, encoding="utf-8")
    spawn_worker(store, job_id)
    return wait_or_report(store, job_id, args)


def require_host_version(binary: Path) -> None:
    reason = agybin.version_problem(agybin.agy_version(binary))
    if reason:
        raise ClassifiedError("agy-missing", f"{reason}; run `agy update`.")


def choose_isolation(args: argparse.Namespace) -> str:
    """Container when it is available, otherwise the host-side policy and sandbox.

    The container check starts a container, so the answer is kept on args
    for the rest of the command."""
    chosen = getattr(args, "chosen_isolation", None)
    if chosen:
        return chosen
    wanted = getattr(args, "isolation", None) or "auto"
    if wanted == "host":
        chosen = "host"
    else:
        reason = container.unavailable_reason()
        if reason is not None and wanted == "container":
            raise ClassifiedError("container", f"container isolation is not available: {reason}.")
        chosen = "host" if reason else "container"
    args.chosen_isolation = chosen
    return chosen


def hook_command(job_dir: Path) -> str:
    return " ".join(shlex.quote(p) for p in (sys.executable, str(SCRIPTS / "agy_hook.py"), str(job_dir)))


def policy_for(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": record["kind"],
        "writable": bool(record["writable"]) and not record.get("read_only_round"),
        "worktree": record["worktree"],
        "read_dirs": record.get("read_dirs") or [],
    }


def spawn_worker(store: JobStore, job_id: str) -> None:
    job_dir = store.job_dir(job_id)
    with open(job_dir / "worker.log", "ab") as log:
        proc = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "_worker", job_id],
            cwd=str(store.root),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
        )
    store.update(job_id, lambda r: r.update({"pid": proc.pid}))


def wait_or_report(store: JobStore, job_id: str, args: argparse.Namespace) -> int:
    if args.background:
        if getattr(args, "codex", False):
            print(f"agy job {job_id} started in the background.\n{result_command(job_id, codex=True)}")
            return 0
        print(
            f"agy job {job_id} started in the background.\n"
            f"Check /agy:status {job_id}; read it with /agy:result {job_id}."
        )
        return 0
    deadline = time.monotonic() + (args.wait_seconds or DEFAULT_WAIT_SECONDS)
    while time.monotonic() < deadline:
        job = store.read(job_id) or {}
        if job.get("status") in TERMINAL_STATUSES:
            print(render.result_text(job, read_json(store.job_dir(job_id) / "result.json")))
            return failure.exit_code(job.get("failure")) if job.get("status") == "failed" else 0
        time.sleep(2)
    job = store.read(job_id) or {}
    print(
        f"agy job {job_id} is still running.\n{render.job_detail(job)}\n"
        f"To wait for the result, run:\n{result_command(job_id, codex=getattr(args, 'codex', False))}"
    )
    return 0


def result_command(job_id: str, *, codex: bool = False) -> str:
    """A shell command that waits for a job and prints its result, for a caller with no slash commands."""
    script = SCRIPTS / ("agy_codex.py" if codex else "agy_companion.py")
    return f"python3 {shlex.quote(str(script))} result {job_id} --wait-seconds 590"


def _without_pasted_material(request: str) -> str:
    """The request without fenced blocks and unfenced diffs.

    Pasting a diff is the remedy for what unreachable_names reports, so the
    refs and paths inside one are not reported. A hunk is as long as its
    "@@" header says, so a deleted line that looks like a header stays inside
    it, and a list item after it does not.
    """
    kept = []
    old = new = 0  # lines of the current hunk still to come
    for line in FENCED.sub("", request).splitlines():
        if old > 0 or new > 0:
            if line.startswith("-"):
                old -= 1
            elif line.startswith("+"):
                new -= 1
            elif not line.startswith("\\"):  # context, which a paste may have trimmed to an empty line
                old, new = old - 1, new - 1
            continue
        header = HUNK_HEADER.match(line)
        if header:
            old, new = (int(count) if count is not None else 1 for count in header.groups())
        elif not line.startswith(DIFF_HEADERS):
            kept.append(line)
    return "\n".join(kept)


def unreachable_names(request: str, root: Path, read_dirs: list[str], in_container: bool) -> list[str]:
    """Git refs and outside paths a task request names that its job cannot reach."""
    prose = _without_pasted_material(request)
    names = MISSING_REF.findall(prose) if in_container else []
    allowed = [str(root), os.path.realpath(root), *read_dirs]
    for path in ABSOLUTE_PATH.findall(prose):
        real = os.path.realpath(path)
        if not any(real == a or real.startswith(a.rstrip("/") + "/") for a in allowed):
            names.append(path)
    return list(dict.fromkeys(names))


def cmd_review(args: argparse.Namespace, kind: str = "review") -> int:
    root = gitctx.repo_root(os.getcwd())
    target = gitctx.resolve_target(root, args.base, args.scope, args.head)  # a bad ref is reported first
    focus = review_instructions(args) or "(none)"
    in_container = choose_isolation(args) == "container"
    context = gitctx.collect_context(root, target)
    if not context["files"]:
        print(f"Nothing to review: no changes found for {target['label']}.")
        return 0
    name = "adversarial-review.md" if kind == "adversarial-review" else "review.md"
    message = template(
        name,
        TARGET_LABEL=target["label"],
        TARGET_SUMMARY=context["summary"],
        USER_FOCUS=focus,
        REVIEW_COLLECTION_GUIDANCE=CONTAINER_REVIEW_GUIDANCE if in_container else HOST_REVIEW_GUIDANCE,
        REVIEW_INPUT=context["content"],
    )
    return start_job(args, kind, message, writable=False, extra={"target": target}, copy_files=context["copy_files"])


def _read_flag_file(path_value: str, flag: str) -> str:
    if not path_value.strip():
        raise UsageError(f"cannot read {flag}: empty path")
    try:
        return _expand_path(path_value, flag).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError) as exc:
        raise UsageError(f"cannot read {flag} {path_value}: {exc}") from exc


def review_instructions(args: argparse.Namespace) -> str:
    """What the caller asked the review to check: the instructions file, then the words after the flags.

    The file is read here, on the host, so it may be anywhere the user can
    read; the job itself sees only the repository.
    """
    parts = []
    if args.instructions_file is not None:
        parts.append(_read_flag_file(args.instructions_file, "--instructions-file"))
    parts.append(" ".join(args.focus or []).strip())
    return "\n\n".join(p for p in parts if p)


def request_text(args: argparse.Namespace) -> str:
    """What the caller asked plan or task to do: --request-file on the host, then trailing words."""
    parts = []
    request_file = getattr(args, "request_file", None)
    if request_file is not None:
        parts.append(_read_flag_file(request_file, "--request-file"))
    parts.append(" ".join(args.request or []).strip())
    return "\n\n".join(p for p in parts if p)


def cmd_plan(args: argparse.Namespace) -> int:
    request = request_text(args)
    if not request:
        raise UsageError("Say what the plan is for, for example: /agy:plan add retry to the uploader")
    gitctx.repo_root(os.getcwd())
    in_container = choose_isolation(args) == "container"
    guidance = CONTAINER_PLAN_GUIDANCE if in_container else HOST_PLAN_GUIDANCE
    message = template("plan.md", USER_REQUEST=request, PLAN_COLLECTION_GUIDANCE=guidance)
    return start_job(args, "plan", message, writable=False)


def _print_unreachable_note(missing: list[str], in_container: bool) -> None:
    if not missing:
        return
    # On stdout, because a subagent relays only stdout to its caller.
    print(
        f"Note: the request names {', '.join(missing)}, which the job cannot reach. "
        + ("Its copy is one commit, with no other refs or history, and " if in_container else "It ")
        + "reads only the repository and any --read-dir directory. Paste the diff or file into the request, "
        "pass --read-dir, or review a ref with `review --base <ref> --head <ref>`.\n"
    )


def cmd_task(args: argparse.Namespace) -> int:
    request = request_text(args)
    if not request:
        raise UsageError("Say what agy should do.")
    store = JobStore(gitctx.repo_root(os.getcwd()))
    if args.resume_last:
        previous = resolve_own_job(
            store,
            args,
            lambda r: (
                r.get("kind") == "task" and r.get("status") in TERMINAL_STATUSES and bool(r.get("conversation_id"))
            ),
        )
        if not previous:
            raise UsageError("No earlier agy task from this session to resume in this repository.")
        if not previous.get("writable") and not args.read_only:
            raise UsageError(
                f"Job {previous['id']} started with --read-only and cannot edit files; pass --read-only "
                "for another read-only round, or start a new task without --read-only."
            )
        in_prev_container = previous.get("isolation") == "container"
        extra_dirs = _resolve_read_dirs(args.read_dir, in_prev_container)
        combined_dirs = list(dict.fromkeys([*(previous.get("read_dirs") or []), *extra_dirs]))
        _print_unreachable_note(
            unreachable_names(request, store.root, combined_dirs, in_prev_container), in_prev_container
        )
        if args.read_only:
            request += CONTAINER_READ_ONLY_NOTE if in_prev_container else HOST_READ_ONLY_NOTE
        return continue_job(store, previous, request, "follow-up", args, read_only=args.read_only)
    in_container = choose_isolation(args) == "container"
    read_dirs = _resolve_read_dirs(args.read_dir, in_container)
    if args.read_only:
        shell_rule = CONTAINER_READ_ONLY_SHELL_RULE if in_container else HOST_SHELL_RULE
        message = template(
            "task.md",
            TASK_ROLE=READ_ONLY_TASK_ROLE,
            SHELL_RULE=shell_rule,
            USER_REQUEST=request,
            WORKING_RULES=READ_ONLY_WORKING_RULES,
            FINAL_REPORT=READ_ONLY_FINAL_REPORT,
        )
    else:
        shell_rule = CONTAINER_SHELL_RULE if in_container else HOST_SHELL_RULE
        message = template(
            "task.md",
            TASK_ROLE=WRITABLE_TASK_ROLE,
            SHELL_RULE=shell_rule,
            USER_REQUEST=request,
            WORKING_RULES=WRITABLE_WORKING_RULES,
            FINAL_REPORT=WRITABLE_FINAL_REPORT,
        )
    _print_unreachable_note(unreachable_names(request, store.root, read_dirs, in_container), in_container)
    return start_job(args, "task", message, writable=not args.read_only)


def continue_job(
    store: JobStore,
    job: dict[str, Any],
    message: str,
    round_kind: str,
    args: argparse.Namespace,
    read_only: bool | None = None,
) -> int:
    """Queue another turn on a finished job. The status check and the change
    to "queued" happen under the job's lock, so two resumes cannot both run.

    read_only applies to this round's tool policy only; None keeps the
    previous round's setting, as an approval resume continues that round. The
    job's own "writable" flag is left alone, so the export after the round and
    /agy:apply still see the edits earlier rounds made."""
    job_id = job["id"]
    wanted_iso = getattr(args, "isolation", None) or "auto"

    def mutate(record: dict[str, Any]) -> None:
        if record.get("status") in ACTIVE_STATUSES:
            raise UsageError(f"Job {job_id} is still running.")
        current_iso = record.get("isolation") or "host"
        if wanted_iso != "auto" and wanted_iso != current_iso:
            raise UsageError(f"Job {job_id} uses {current_iso} isolation; start a new job to use {wanted_iso}.")
        extra_dirs = _resolve_read_dirs(getattr(args, "read_dir", None), current_iso == "container")
        # Checked under the lock that cleanup and expiry also take.
        worktree = record.get("worktree")
        if not worktree or not Path(worktree).is_dir():
            raise UsageError(f"Job {job_id}'s working copy no longer exists; start a new job.")
        if current_iso == "host":
            require_host_version(Path(record["agy_bin"]))
        else:
            reason = agybin.version_problem(container.image_version())
            if reason:
                raise ClassifiedError(
                    "container", f"image {container.image_name()}: {reason}; rebuild with containers/build.sh."
                )
        finished = parse_iso(record.get("completed_at"))
        merged_dirs = list(dict.fromkeys([*(record.get("read_dirs") or []), *extra_dirs]))
        record.update(
            {
                "status": "queued",
                "phase": f"resuming ({round_kind})",
                "next_round_kind": round_kind,
                "error": None,
                "failure": None,
                "queued_commands": [],
                "read_dirs": merged_dirs,
                "pending_approval_wait_s": round(time.time() - finished.timestamp(), 1) if finished else None,
            }
        )
        if getattr(args, "model", None):
            record["model"] = agybin.resolve_model(
                args.model,
                caller=getattr(args, "caller_model", None),
                settings=model_settings(current_iso),
                current=record.get("model"),
            )
        if read_only is not None:
            record["read_only_round"] = read_only
        record["generation"] = int(record.get("generation") or 1) + 1
        record.update({"pid": None, "agy_pid": None})

    record = store.update(job_id, mutate)
    if record.get("read_dirs"):
        message += "\n\nYou may also read files in: " + ", ".join(record["read_dirs"]) + "\n"
    job_dir = store.job_dir(job_id)
    if record.get("isolation") != "container":
        try:
            gitctx.install_hooks(Path(record["worktree"]), hook_command(job_dir))  # .agents holds only the hook again
        except Exception as exc:
            error = f"could not prepare the working copy: {exc}"
            # As when a worker fails: the earlier round's result and patch
            # must not pass for this failed round's.
            for name in ("result.json", "changes.patch"):
                (job_dir / name).unlink(missing_ok=True)
            store.update(
                job_id,
                lambda r: r.update(
                    {
                        "status": "failed",
                        "phase": "failed",
                        "error": error,
                        "failure": failure.classify("exited", error),
                    }
                ),
            )
            raise
        write_json(job_dir / "policy.json", policy_for(record))
    (job_dir / "next-message.md").write_text(message, encoding="utf-8")
    spawn_worker(store, job_id)
    return wait_or_report(store, job_id, args)


def cmd_resume(args: argparse.Namespace) -> int:
    store = JobStore(gitctx.repo_root(os.getcwd()))
    job = store.resolve(args.job)
    if not job:
        raise UsageError("No matching agy job.")
    if not job.get("conversation_id"):
        raise UsageError(f"Job {job['id']} has no agy conversation to resume.")
    has_file = args.results_file is not None
    results = _read_flag_file(args.results_file, "results_file") if has_file else ""
    note = " ".join(args.note or []).strip()
    if not results and not note:
        raise UsageError("Give the command output file, a note, or both.")
    if not has_file:
        message = (
            "The commands you requested were not run. Continue the task from where you stopped "
            f"following the note below and give the complete final answer.\n\nNote from Claude: {note}\n"
        )
    else:
        shown = results or "(the results file contained no output)"
        body = f"{shown}\n\nNote from Claude: {note}" if note else shown
        message = template("resume-with-results.md", COMMAND_RESULTS=body)
    return continue_job(store, job, message, "approval-resume", args)


# worker -------------------------------------------------------------------------


class _Stopped(Exception):
    pass


def _on_sigterm(signum: int, frame: Any) -> None:
    raise SystemExit(143)  # unwinds run_turn, whose finally stops agy


def cmd_worker(args: argparse.Namespace) -> int:
    signal.signal(signal.SIGTERM, _on_sigterm)
    store = JobStore(os.getcwd())

    def claim(record: dict[str, Any]) -> None:
        if record.get("status") != "queued":
            raise _Stopped(record.get("status"))
        record.update({"status": "running", "phase": "running", "pid": os.getpid()})

    try:
        store.update(args.job, claim)
    except _Stopped:
        return 0  # cancelled before the worker started
    runner: JobRunner | None = None
    try:
        runner = JobRunner(store, args.job)
        runner.run()
    except Exception as exc:  # noqa: BLE001 - record any failure on the job
        error = f"worker error: {exc}"
        # An earlier round's result and patch must not pass for this round's;
        # files this round already wrote stay.
        written = runner.written if runner else set()
        for name in ("result.json", "changes.patch"):
            if name not in written:
                (store.job_dir(args.job) / name).unlink(missing_ok=True)

        def fail(record: dict[str, Any]) -> None:
            if record.get("status") != "cancelled":
                record.update(
                    {
                        "status": "failed",
                        "phase": "failed",
                        "error": error,
                        "failure": failure.classify("exited", error),
                        "pid": None,
                        "completed_at": now_iso(),
                    }
                )

        store.update(args.job, fail)
        raise
    return 0


# status, result, cancel, apply, cleanup -------------------------------------------


def _session_filter(args: argparse.Namespace):
    session = os.environ.get(SESSION_ID_ENV)
    if getattr(args, "all", False) or not session:
        return None
    return lambda r: r.get("session_id") in (session, None)


def store_for(args: argparse.Namespace) -> JobStore:
    """This repository's job store, or, for a job id that is not in it, the
    store of the checkout that ran that job, so a job id works from anywhere."""
    store = JobStore(os.getcwd())
    reference = getattr(args, "job", None)
    if reference and store.resolve(reference) is None:
        return store_holding(reference) or store
    return store


def resolve_own_job(store: JobStore, args: argparse.Namespace, predicate) -> dict[str, Any] | None:
    """Resolve a job id, or with none given the newest match from this Claude session.

    Job state is shared per repository, so without the session filter a bare
    /agy:cancel or /agy:apply in one session would act on another session's
    job, one that /agy:status in this session does not even show.
    """
    reference = getattr(args, "job", None)
    if reference:
        return store.resolve(reference, predicate)
    own = _session_filter(args)
    if own is None:
        return store.resolve(None, predicate)
    return store.resolve(None, lambda r: own(r) and (predicate is None or predicate(r)))


def cmd_status(args: argparse.Namespace) -> int:
    store = store_for(args)
    if args.job:
        job = store.resolve(args.job)
        if not job:
            raise UsageError(f"No agy job matches {args.job}.")
        stored = read_json(store.job_dir(job["id"]) / "result.json")
        print(render.job_detail(job, stored))
        return 0
    predicate = _session_filter(args)
    every = store.list_jobs()
    jobs = [j for j in every if predicate is None or predicate(j)][:10]
    print(render.status_table(jobs))
    hidden = len(every) - len([j for j in every if predicate is None or predicate(j)])
    if hidden:
        print(f"\n{hidden} job(s) from other Claude sessions are hidden; /agy:status --all shows them.")
    return 0


def cmd_result(args: argparse.Namespace) -> int:
    store = store_for(args)
    job = resolve_own_job(store, args, None if args.job else (lambda r: r.get("status") in TERMINAL_STATUSES))
    if not job:
        raise UsageError("No finished agy job from this session found.")
    deadline = time.monotonic() + (args.wait_seconds or 0)
    while job.get("status") in ACTIVE_STATUSES and time.monotonic() < deadline:
        time.sleep(2)
        job = store.read(job["id"]) or job
    if job.get("status") in ACTIVE_STATUSES:
        # A resumed job still holds its previous round's result, which must not be read as this round's.
        print(
            f"agy job {job['id']} is still running.\n{render.job_detail(job)}\n"
            f"To wait for the result, run:\n{result_command(job['id'], codex=getattr(args, 'codex', False))}"
        )
        return 0
    print(render.result_text(job, read_json(store.job_dir(job["id"]) / "result.json")))
    waited_and_failed = args.wait_seconds and job.get("status") == "failed"
    return failure.exit_code(job.get("failure")) if waited_and_failed else 0


def cmd_cancel(args: argparse.Namespace) -> int:
    store = store_for(args)
    job = resolve_own_job(store, args, lambda r: r.get("status") in ACTIVE_STATUSES)
    if not job:
        raise UsageError("No running agy job from this session to cancel.")
    unchecked = cancel_job(store, job, "cancelled")
    if unchecked:
        pids = ",".join(str(pid) for pid in unchecked)
        print(
            f"Cancelled {job['id']}, but ps could not check some of its processes, which may "
            f"still be running. Check with `ps -o pid,args -p {pids}`.",
            file=sys.stderr,
        )
        return 1
    print(f"Cancelled {job['id']}.")
    return 0


def cancel_job(store: JobStore, job: dict[str, Any], phase: str, grace: float = 10) -> list[int]:
    """Stop a job, and return the pids of its processes that ps could not
    check (empty when all were checked). It is marked "stopping" (still
    active, so nothing can resume it) while its processes are stopped, then
    "cancelled". Process ids are cleared only if no newer worker generation
    has started meanwhile."""
    return stop_jobs([(store, job)], phase, grace).get(job["id"], [])


def stop_jobs(jobs: list[tuple[JobStore, dict[str, Any]]], phase: str, grace: float) -> dict[str, list[int]]:
    """Stop several jobs at once, so the total wait is one grace period.

    Returns, by job id, the pids that ps could not check, so may still be
    running; the job's phase says so.
    """
    claimed = []
    for store, job in jobs:

        def begin(record: dict[str, Any]) -> None:
            if record.get("status") in ACTIVE_STATUSES:
                record.update({"status": "stopping", "phase": phase})

        record = store.update(job["id"], begin)
        if record.get("status") == "stopping":
            claimed.append((store, record))
    unchecked: dict[str, list[int]] = {}
    terminate_job_processes([record for _, record in claimed], grace=grace, unchecked=unchecked)
    for store, record in claimed:
        generation = record.get("generation")
        ended = phase
        if record["id"] in unchecked:
            pids = ", ".join(str(pid) for pid in unchecked[record["id"]])
            ended = f"{phase}; ps could not check its processes ({pids}), which may still be running"

        def finish(current: dict[str, Any], generation: Any = generation, ended: str = ended) -> None:
            if current.get("generation") == generation:
                current.update(
                    {"status": "cancelled", "phase": ended, "completed_at": now_iso(), "pid": None, "agy_pid": None}
                )

        store.update(record["id"], finish)
        if record.get("kind") != "task" and record.get("worktree") and Path(record["worktree"]).exists():
            gitctx.remove_worktree(record["repo_root"], Path(record["worktree"]))
    return unchecked


def cmd_apply(args: argparse.Namespace) -> int:
    store = JobStore(os.getcwd())
    if args.job and store.resolve(args.job) is None:
        elsewhere = store_holding(args.job)
        if elsewhere is not None:
            # apply patches the checkout that ran the job, so it runs only there.
            raise UsageError(f"Job {args.job} ran in {elsewhere.root}; run apply from that checkout.")
    job = resolve_own_job(store, args, lambda r: bool(r.get("writable")) and r.get("status") in TERMINAL_STATUSES)
    if not job:
        raise UsageError("No finished agy task with changes from this session found.")
    job_dir = store.job_dir(job["id"])
    patch_file = job_dir / "changes.patch"
    patch = patch_file.read_bytes() if patch_file.exists() else b""
    if not patch.strip():
        print(f"Job {job['id']} changed no files.")
        return 0
    stored = read_json(job_dir / "result.json", {}) or {}
    root = Path(job["repo_root"])
    tree = read_json(job_dir / "baseline-tree.json")
    if not isinstance(tree, dict):
        raise UsageError(
            f"Job {job['id']} has no baseline snapshot (it predates this version of the plugin), so the drift "
            f"check cannot run. Review the patch at {patch_file} and apply it with git apply, or start a new job."
        )
    moved = gitctx.baseline_mismatches(root, tree, stored.get("changed_paths") or [])
    if moved and not args.force:
        listed = "\n".join(f"- {path}" for path in moved)
        raise UsageError(
            "These files changed in your checkout since the job started, so applying could mix "
            f"agy's edits with different code:\n{listed}\nReview the patch at {patch_file}, "
            "or rerun with --force to apply it anyway."
        )
    gitctx.apply_patch(root, patch, check_only=True)
    if args.check:
        print(f"The changes from {job['id']} apply cleanly.")
        return 0
    gitctx.apply_patch(root, patch)
    store.update(job["id"], lambda r: r.update({"applied_at": now_iso()}))
    print(f"Applied the changes from {job['id']} to {root}. Review them with git diff.")
    return 0


def cmd_cleanup(args: argparse.Namespace) -> int:
    store = JobStore(os.getcwd())
    removed = []
    for job in store.list_jobs():
        if args.job and not job["id"].startswith(args.job):
            continue
        if drop_finished_working_copy(store.job_dir(job["id"])):
            removed.append(job["id"])
    print(f"Removed {len(removed)} job working cop{'y' if len(removed) == 1 else 'ies'}.")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    """Write a container job's patch and file list. Runs inside the container."""
    out = Path(args.out_dir)
    changes = gitctx.export_changes(Path(args.worktree), args.baseline, gitctx.CONTAINER_EXPORT_EXCLUDES)
    (out / "changes.patch").write_bytes(changes["patch"])
    write_json(out / "changes.json", {"name_status": changes["name_status"], "paths": changes["paths"]})
    return 0


# Claude Code session hooks -----------------------------------------------------------


def cmd_session_start(args: argparse.Namespace) -> int:
    """Give the session's commands the same state root the hooks use.

    Claude Code exports CLAUDE_PLUGIN_DATA to hook processes but not to the
    Bash tool, so without this the session-end hook would look for the
    session's jobs in a directory the commands never wrote to.
    """
    payload = json.load(sys.stdin) if not sys.stdin.isatty() else {}
    env_file = os.environ.get("CLAUDE_ENV_FILE")
    session = payload.get("session_id")
    if env_file and session:
        with open(env_file, "a", encoding="utf-8") as handle:
            handle.write(f"export {SESSION_ID_ENV}={shlex.quote(session)}\n")
            handle.write(f"export {STATE_DIR_ENV}={shlex.quote(str(state_root()))}\n")
    return 0


def cmd_session_end(args: argparse.Namespace) -> int:
    """Stop every active job the ending Claude session started, in any repository."""
    payload = json.load(sys.stdin) if not sys.stdin.isatty() else {}
    session = payload.get("session_id")
    if not session:
        return 0
    try:
        entries = read_json(session_index(session), []) or []
        targets = []
        for entry in entries:
            store = JobStore(entry["repo_root"])
            job = store.read(entry["job_id"])
            if job and job.get("session_id") == session and job.get("status") in ACTIVE_STATUSES:
                targets.append((store, job))
        stop_jobs(targets, "session ended", grace=3)  # the hook allows ten seconds in total
        session_index(session).unlink(missing_ok=True)
    except Exception:  # noqa: BLE001 - never block session shutdown
        return 0
    return 0


# argument parsing -----------------------------------------------------------------------


def add_run_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--model", help="a level (low, medium, high, xhigh), a family (neon), both (neon-high), or a full model ID"
    )
    parser.add_argument(
        "--caller-model", help="the calling Claude model's ID; picks a new job's Gemini model when --model is absent"
    )
    parser.add_argument("--background", action="store_true", help="return at once with the job id")
    parser.add_argument("--wait", action="store_true", help="wait for the result (the default)")
    parser.add_argument("--wait-seconds", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--stall-seconds", type=float, help=argparse.SUPPRESS)
    parser.add_argument("--no-os-sandbox", action="store_true", help="host mode: do not wrap agy in sandbox-exec")
    parser.add_argument(
        "--isolation",
        choices=ISOLATION_CHOICES,
        default="auto",
        help="container when available (the default), or host for the hook policy and sandbox-exec",
    )
    parser.add_argument(
        "--read-dir", action="append", help="another directory agy may read (repeatable); reads are otherwise confined"
    )


def review_job_id(value: str) -> str:
    if not re.fullmatch(r"(?:adversarial-)?review-[0-9]{8}-[0-9]{6}-[a-f0-9]{4}", value):
        raise argparse.ArgumentTypeError("pass the full review job ID")
    return value


def build_parser(*, codex: bool = False) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agy_codex.py" if codex else "agy_companion.py", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    setup = sub.add_parser("setup")
    setup.add_argument("--json", action="store_true")
    setup.set_defaults(func=cmd_setup)

    for name in ("review", "adversarial-review"):
        review = sub.add_parser(name)
        add_run_options(review)
        review.add_argument("--base")
        review.add_argument("--head", help="review this ref, not what is checked out, for example a fetched PR")
        review.add_argument("--scope", default="auto")
        review.add_argument("--instructions-file", help="a file of context and things to check, read on the host")
        review.add_argument("focus", nargs="*", help="context and things to check")
        review.set_defaults(func=lambda a, kind=name: cmd_review(a, kind))

    for name, func in (("status", cmd_status), ("result", cmd_result), ("cancel", cmd_cancel)):
        p = sub.add_parser(name)
        if codex:
            p.add_argument("job", type=review_job_id)
        else:
            p.add_argument("job", nargs="?")
        if name == "status" and not codex:
            p.add_argument("--all", action="store_true")
        if name == "result":
            p.add_argument("--wait-seconds", type=int, help="wait this long for a job that is still running")
        p.set_defaults(func=func)

    if codex:
        parser.set_defaults(codex=True)
        return parser

    plan = sub.add_parser("plan")
    add_run_options(plan)
    plan.add_argument("--request-file", help="a file holding the plan request, read on the host")
    plan.add_argument("request", nargs="*")
    plan.set_defaults(func=cmd_plan)

    task = sub.add_parser("task")
    add_run_options(task)
    task.add_argument("--read-only", action="store_true")
    task.add_argument("--resume-last", action="store_true")
    task.add_argument("--request-file", help="a file holding the task request, read on the host")
    task.add_argument("request", nargs="*")
    task.set_defaults(func=cmd_task)

    resume = sub.add_parser("resume")
    add_run_options(resume)
    resume.add_argument("job")
    resume.add_argument("results_file", nargs="?")
    resume.add_argument("--note", nargs="*")
    resume.set_defaults(func=cmd_resume)

    apply = sub.add_parser("apply")
    apply.add_argument("job", nargs="?")
    apply.add_argument("--check", action="store_true")
    apply.add_argument("--force", action="store_true")
    apply.set_defaults(func=cmd_apply)

    cleanup = sub.add_parser("cleanup")
    cleanup.add_argument("job", nargs="?")
    cleanup.set_defaults(func=cmd_cleanup)

    worker = sub.add_parser("_worker")
    worker.add_argument("job")
    worker.set_defaults(func=cmd_worker)

    export = sub.add_parser("_export")
    export.add_argument("worktree")
    export.add_argument("baseline")
    export.add_argument("out_dir")
    export.set_defaults(func=cmd_export)

    sub.add_parser("session-start").set_defaults(func=cmd_session_start)
    sub.add_parser("session-end").set_defaults(func=cmd_session_end)
    return parser


def _split_prose_preserving_quotes(raw: str) -> list[str]:
    """Split on whitespace while keeping balanced "..." and standalone '...' tokens intact.

    Used for unquoted prose apostrophes and incomplete shell quoting.
    Unlike shlex.shlex, '#' is never a comment and backslashes outside double
    quotes stay literal, so '#91' and 'C:\\temp' survive unchanged.
    """
    tokens: list[str] = []
    buf: list[str] = []
    index = 0
    length = len(raw)
    while index < length:
        ch = raw[index]
        if ch.isspace():
            if buf:
                tokens.append("".join(buf))
                buf = []
            index += 1
            continue
        if ch == '"' and (not buf or buf[-1] == "="):
            end = index + 1
            inner: list[str] = []
            closed = False
            while end < length:
                cur = raw[end]
                if cur == "\\" and end + 1 < length and raw[end + 1] in ('"', "\\"):
                    inner.append(raw[end + 1])
                    end += 2
                    continue
                if cur == '"':
                    closed = True
                    end += 1
                    break
                inner.append(cur)
                end += 1
            if closed and (end == length or raw[end].isspace()):
                buf.extend(inner)
                index = end
                continue
        if ch == "'" and (not buf or buf[-1] == "="):
            close = raw.find("'", index + 1)
            if close != -1 and (close + 1 == length or raw[close + 1].isspace()):
                buf.append(raw[index + 1 : close])
                index = close + 1
                continue
        buf.append(ch)
        index += 1
    if buf:
        tokens.append("".join(buf))
    return tokens


# Skip escaped characters and complete quoted strings when looking for a
# possessive or contraction. Apostrophes inside those strings are shell data.
_RAW_QUOTES = re.compile(r"""\\.|"(?:\\.|[^"\\])*" | (?<!\w)'[^']*' | (?P<prose>(?<=\w)')""", re.VERBOSE)


def _split_raw(raw: str) -> list[str]:
    if any(match.lastgroup == "prose" for match in _RAW_QUOTES.finditer(raw)):
        return _split_prose_preserving_quotes(raw)
    try:
        return shlex.split(raw)
    except ValueError:
        return _split_prose_preserving_quotes(raw)


def split_arguments(argv: list[str]) -> list[str]:
    """Expand `--raw <string>` into the words of the string.

    Slash commands pass the user's whole argument line as one string after
    --raw, and it is split like a shell would split it. A stray apostrophe in
    prose preserves double-quoted arguments before falling back to whitespace.
    Every other argument is kept as it is, so a quoted path with spaces stays
    one argument.
    """
    out: list[str] = []
    items = iter(argv)
    for arg in items:
        if arg != "--raw":
            out.append(arg)
            continue
        out += _split_raw(next(items, ""))
    return out


def main(argv: list[str] | None = None, *, codex: bool = False) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    args = build_parser(codex=codex).parse_args(argv if codex else split_arguments(argv))
    try:
        return int(args.func(args) or 0)
    except ClassifiedError as exc:
        print(f"agy: {exc}", file=sys.stderr)
        return failure.exit_code(exc.failure)
    except (UsageError, gitctx.GitError) as exc:
        print(f"agy: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
