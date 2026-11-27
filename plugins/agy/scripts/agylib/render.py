"""Text output for status, results and job details."""

from __future__ import annotations

import json
import os
import re
from typing import Any
from urllib.parse import unquote

from . import failure
from .state import now_iso, parse_iso

WEB_OUTCOMES = ("ran", "failed", "unfinished")

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
CANNOT_REVIEW = "cannot-review"
# agy writes references as file:// links, bare or as a Markdown link's
# target, with the line as a #L12 or #L12-L15 fragment.
FILE_URL = r"file://(/[^\s)#>\]]*)(?:#L(\d+)(?:-L?\d+)?)?"
FILE_LINKS = (re.compile(r"\[[^\]\n]*\]\(" + FILE_URL + r"\)"), re.compile(FILE_URL))


def elapsed(job: dict[str, Any]) -> str:
    start = parse_iso(job.get("created_at"))
    end = parse_iso(job.get("completed_at")) if job.get("status") not in ("running", "resuming", "queued") else None
    end = end or parse_iso(now_iso())
    if not start or not end:
        return "-"
    seconds = int((end - start).total_seconds())
    minutes, seconds = divmod(max(seconds, 0), 60)
    return f"{minutes}m{seconds:02d}s"


def _stall_count(job: dict[str, Any]) -> int:
    rounds = job.get("rounds") or []
    return sum(len(r.get("stalls") or []) for r in rounds) + len(job.get("current_stalls") or [])


def status_table(jobs: list[dict[str, Any]]) -> str:
    if not jobs:
        return "No agy jobs for this repository."
    header = "| Job | Kind | Status | Phase | Model | Isolation | Elapsed | Rounds | Stalls | Queued commands |\n"
    header += "|---|---|---|---|---|---|---|---|---|---|\n"
    rows = []
    for job in jobs:
        rows.append(
            "| {id} | {kind} | {status} | {phase} | {model} | {isolation} | {elapsed} | {rounds} | {stalls} "
            "| {queued} |".format(
                id=job.get("id"),
                kind=job.get("kind"),
                status=job.get("status"),
                phase=str(job.get("phase") or "").replace("|", "/"),
                model=model_label(job.get("model")),
                isolation=job.get("isolation") or "host",
                elapsed=elapsed(job),
                rounds=len(job.get("rounds") or []),
                stalls=_stall_count(job),
                queued=len(job.get("queued_commands") or []),
            )
        )
    return header + "\n".join(rows)


def model_label(model: str | None) -> str:
    """A job runs without a model ID when agy chose; agy does not report which."""
    return model or "agy default"


def rounds_table(job: dict[str, Any]) -> str:
    rounds = job.get("rounds") or []
    if not rounds:
        return ""
    lines = [
        "| Round | Kind | Model | Duration | Wait for Claude | Tokens | Stalls | Queued |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for index, item in enumerate(rounds, 1):
        usage = item.get("usage") or {}
        wait = item.get("approval_wait_s")
        lines.append(
            f"| {index} | {item.get('kind')} | {model_label(item.get('final_model'))} | {item.get('duration_s')} s | "
            f"{'-' if wait is None else str(wait) + ' s'} | {usage.get('total_tokens', '-')} | "
            f"{len(item.get('stalls') or [])} | {len(item.get('queued_commands') or [])} |"
        )
    return "\n".join(lines)


def job_detail(job: dict[str, Any], stored: dict[str, Any] | None = None) -> str:
    lines = [
        f"Job: {job.get('id')}",
        f"Kind: {job.get('kind')}",
        f"Status: {job.get('status')} ({job.get('phase')})",
        f"Model: {model_label(job.get('model'))}",
        f"Isolation: {job.get('isolation') or 'host'}",
        f"Elapsed: {elapsed(job)}",
        f"Conversation: {job.get('conversation_id') or '-'}",
        f"Working copy: {job.get('worktree')}",
    ]
    progress = job.get("progress") or {}
    if progress:
        lines.append(
            f"Progress: step {progress.get('steps')}, {progress.get('tools')} tool calls, "
            f"last tool {progress.get('last_tool') or '-'}"
        )
    for stall in job.get("current_stalls") or []:
        lines.append(
            f"Stall: {model_label(stall.get('model'))} at {stall.get('at')}: {stall.get('reason')}; "
            f"resumed on {model_label(stall.get('resumed_on'))}"
        )
    if job.get("error"):
        lines.append(f"Error: {job['error']}")
    if job.get("failure"):
        lines.append(f"Failure: {job['failure']} (exit {failure.exit_code(job['failure'])})")
    table = rounds_table(job)
    if table:
        lines += ["", table]
    lines += ["", next_steps(job, stored)]
    return "\n".join(lines)


def next_steps(job: dict[str, Any], stored: dict[str, Any] | None = None) -> str:
    job_id = job.get("id")
    status = job.get("status")
    command = "agy_codex.py " if job.get("client") == "codex" else "/agy:"
    if status in ("queued", "running", "resuming"):
        return f"Next: {command}status {job_id} for progress, {command}cancel {job_id} to stop."
    if status == "needs-approval":
        commands = "\n".join(f"- `{c}`" for c in job.get("queued_commands") or [])
        return (
            f"agy requested approval.\n{commands or 'agy recorded no commands.'}\n"
            f"Run approved commands in {job.get('worktree')}, save their output, "
            f"then use `{command}resume {job_id} <file>`. "
            f"If you skip the commands, use `{command}resume {job_id} --note <reason>`."
        )
    if "has_changes" in job:
        has_changes = bool(job["has_changes"])
    elif isinstance(stored, dict):
        has_changes = bool(stored.get("changed_files") or stored.get("changed_paths"))
    else:
        has_changes = True
    if status == "completed" and job.get("writable") and has_changes:
        return (
            f"Next: {command}result {job_id} to read the report and changes; "
            f"{command}apply {job_id} to apply them to your checkout."
        )
    return f"Next: {command}result {job_id}"


def _review_text(review: dict[str, Any]) -> str:
    if review.get("verdict") == CANNOT_REVIEW:
        # No verdict line at all, so a caller that reads only that line cannot take this for an approval.
        # ... and on one line, so nothing in agy's summary can stand as a line of its own.
        return f"Cannot review: {' '.join(str(review.get('summary', '')).split())}\nNo verdict was given."
    lines = [f"Verdict: {review.get('verdict')}", "", str(review.get("summary", "")).strip(), ""]
    findings = sorted(review.get("findings") or [], key=lambda f: SEVERITY_ORDER.get(str(f.get("severity")), 9))
    if not findings:
        lines.append("No findings.")
    for item in findings:
        lines += [
            f"[{item.get('severity')}] {item.get('title')}",
            f"  {item.get('file')}:{item.get('line_start')}-{item.get('line_end')} "
            f"(confidence {item.get('confidence')})",
            f"  {str(item.get('body', '')).strip()}",
            f"  Recommendation: {str(item.get('recommendation', '')).strip()}",
            "",
        ]
    steps = review.get("next_steps") or []
    if steps:
        lines.append("Next steps:")
        lines += [f"- {s}" for s in steps]
    return "\n".join(lines).rstrip()


def result_text(job: dict[str, Any], stored: dict[str, Any] | None) -> str:
    """Render a result, with paths in the job's working copy mapped back to the repository."""
    text = _result_text(job, stored)
    worktree, root = job.get("worktree"), job.get("repo_root")
    # A read-only job's working copy matches the repository, so its paths can
    # point at the user's files. A task's edits exist only in its working copy
    # until applied, so its paths stay as they are.
    if worktree and root and not job.get("writable"):
        for variant in {worktree, os.path.realpath(worktree)}:
            text = text.replace(variant, root)
        for pattern in FILE_LINKS:
            text = pattern.sub(lambda match: _relative_reference(match, root), text)
        target = job.get("target") or {}
        head = target.get("head_commit") or target.get("head")
        if head:
            # The user's checkout holds some other commit, so a path there is not the reviewed file.
            text = re.sub(r"(?<![\w.:/-])" + re.escape(root.rstrip("/") + "/"), "", text)
            text += f"\nPaths and lines are as of {head}; read a file with `git show {head}:<path>`."
    # Added after the path rewriting, which would otherwise decode and alter a query.
    web = "\n".join(_web_lines(stored or {}))
    return f"{text}{web}\n\n{next_steps(job, stored)}"


def _relative_reference(match: re.Match[str], root: str) -> str:
    """A file:// link into the repository as path:line, which Claude Code shows as a working link."""
    path = os.path.normpath(unquote(match.group(1)))
    prefix = root.rstrip("/") + "/"
    if not path.startswith(prefix):
        return match.group(0)
    line = match.group(2)
    return path[len(prefix) :] + (f":{line}" if line else "")


def _web_lines(stored: dict[str, Any]) -> list[str]:
    """The round's web searches, as agy's event stream reported them.

    Each query is quoted as ASCII JSON on one line, so text agy chose cannot
    pose as a line of the report. agy's events carry the queries, not the
    results, so the URLs agy cites come only from its reply. A search that
    started but never reported an outcome, because the turn was stopped, is
    listed as unfinished. Earlier rounds of the same conversation are not
    covered.
    """
    searches = stored.get("web_searches") or []
    state = "on" if stored.get("web") else "off"
    if not searches:
        return ["", "Web search was on, but agy ran no searches in this round."] if state == "on" else []
    counts = {outcome: sum(1 for s in searches if s.get("outcome") == outcome) for outcome in WEB_OUTCOMES}
    summary = ", ".join(f"{counts[outcome]} {outcome}" for outcome in WEB_OUTCOMES)
    lines = ["", f"Web searches in this round (web search {state} for this job; {summary}):"]
    for search in searches:
        query = json.dumps(str(search.get("query", "")))
        line = f"- {search.get('outcome')}: {query}"
        if search.get("error"):
            line += " (" + json.dumps(str(search["error"]))[1:-1] + ")"
        lines.append(line)
    if not counts["ran"]:
        lines.append("No search in this round was reported as successful.")
    return lines


def _result_text(job: dict[str, Any], stored: dict[str, Any] | None) -> str:
    head = f"Job {job.get('id')} ({job.get('kind')}, {job.get('status')}, model {model_label(job.get('model'))})"
    if not stored:
        if not job.get("error"):
            return f"{head}\nNo result yet."
        # The worker failed before agy's reply was stored.
        parts = [head, "", f"Error: {job['error']}"]
        if job.get("failure"):
            parts.append(f"Failure: {job['failure']} (exit {failure.exit_code(job['failure'])})")
        return "\n".join(parts)
    parts = [head, ""]
    if job.get("error"):
        parts += [f"Error: {job['error']}", ""]
    if job.get("failure"):
        parts += [f"Failure: {job['failure']} (exit {failure.exit_code(job['failure'])})", ""]
        if stored.get("stderr_tail"):
            parts += ["agy stderr (last lines):", stored["stderr_tail"].strip(), ""]
    kind = job.get("kind")
    if kind in ("review", "adversarial-review") and stored.get("review"):
        parts.append(_review_text(stored["review"]))
    elif kind == "plan" and stored.get("plan_markdown"):
        parts += [f"Plan file: {stored.get('plan_path')}", "", stored["plan_markdown"].strip()]
        if stored.get("response"):
            parts += ["", "agy's reply:", stored["response"].strip()]
    else:
        if stored.get("parse_error"):
            parts += [f"Could not read a structured review: {stored['parse_error']}. Raw reply:", ""]
        parts.append(str(stored.get("response") or "(empty reply)").strip())
    if stored.get("changed_files"):
        parts += ["", "Files changed in the job's working copy:", stored["changed_files"]]
    elif job.get("writable") and not job.get("error"):
        parts += ["", "agy changed no files."]
    return "\n".join(parts)
