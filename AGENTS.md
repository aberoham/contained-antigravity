# AGENTS.md

Repository guidance for coding and review agents working on `contained-antigravity-for-claude-code`.

## Runtime and portability constraints

- `plugins/agy` runs on Python 3.9 or later using the standard library only (including macOS Command Line Tools `/usr/bin/python3`). Do not import third-party packages under `plugins/agy/` and keep `from __future__ import annotations` in Python modules.
- `plugins/agy` drops into external plugin marketplaces unchanged (`tests/test_plugin_structure.py`):
  - Do not add `version` fields to `plugins/agy/.claude-plugin/plugin.json` or `.claude-plugin/marketplace.json`, and keep `description` and `author` identical in both files.
  - Quote any YAML Ain't Markup Language (YAML) frontmatter scalar containing `: ` across `agents/*.md`, `commands/*.md` and `skills/*/SKILL.md` (standard-library tests validate frontmatter without PyYAML).
  - Every `plugins/agy/skills/*/SKILL.md` requires a `description` frontmatter field and a `## Gotchas` heading.

## Running checks without slowdowns or Docker lock collisions

Continuous Integration (CI) in `.github/workflows/ci.yml` runs a matrix of `ubuntu-latest` and `macos-latest` across Python `3.9` and `3.13` (note that `ci.yml` pins tool versions independently of `pyproject.toml`):

```text
uvx ruff@0.16.6 check plugins tests
uvx ruff@0.16.6 format --check plugins tests
uvx --python 3.13 --with pytest==9.1.1 ty@0.0.78 check plugins tests
uv run --no-project --python 3.9 --with 'pytest>=8.4,<10' pytest -q
```

- **Scope iterative test runs.** Unscoped `pytest` exercises real stall, session-expiry and process-termination timers, macOS `sandbox-exec`, and a Docker image build in `tests/test_real_container.py`. During iterative edits and review passes, run targeted files or `-k` filters:
  - Structure and shell-quoting checks: `pytest -q tests/test_plugin_structure.py tests/test_shell_snippets.py`
  - Companion and prompt checks: `pytest -q tests/test_companion.py tests/test_review_refs.py tests/test_failure.py`
- **Never run `tests/test_real_container.py` in two processes at once.** Its module fixture runs `docker image prune --force --filter label=agy-cc.image=test`, and Docker fails concurrent prune calls with `a prune operation is already running`. Run the full suite (including `test_real_container.py`) once serially before opening or merging a pull request.
- **Keep external review passes fast.** When running `codex exec -s read-only` (or another review agent) over a diff here, tell it in the prompt to run targeted test files rather than the unscoped suite, and do not run `test_real_container.py` on the host at the same time.

## Multi-round job invariants (`agy_companion.py`, `agylib/`)

Whenever changing flags, prompts, permissions, mounts or result rendering for `task`, trace all four round paths in both `container` and `host` isolation:

1. **Fresh writable `task`:** `writable=True`, `mode="accept-edits"`, working copy writable, patch exported on completion (`has_changes` gates the `/agy:apply` next-step hint in `render.py`, while `cmd_apply` itself reads `changes.patch`).
2. **Fresh read-only `task --read-only`:** `writable=False`, `mode=None`, read-only prompt contract (`READ_ONLY_TASK_ROLE` / `READ_ONLY_FINAL_REPORT`), no export container. Workspace writes are blocked by a read-only bind mount in `container` mode and by the `PreToolUse` hook policy in `host` mode (`sandbox-exec` in host mode still lists the worktree in `writable_paths`, so host read-only enforcement is hook-based rather than an operating-system guarantee).
3. **Follow-up `task --resume-last`:**
   - Round permissions come from `bool(job["writable"]) and not job.get("read_only_round")` in both `policy_for()` and `JobRunner._command()` (so a `--read-only` follow-up on a writable job drops `--mode accept-edits`, while `job["writable"]` stays `True` so earlier edits remain exportable).
   - A job started with `writable=False` stays read-only for life; resuming it without `--read-only` must fail fast with exit 2 (`UsageError`).
   - Follow-ups validate and persist newly passed `--read-dir` paths, reject conflicting explicit `--isolation` flags, and run `unreachable_names`.
4. **Approval `resume`:** keeps the interrupted round's `read_only_round` and `isolation`, retains existing `read_dirs` (merging any newly passed `--read-dir` directories), requires non-empty `results` or `--note`, and branches on `args.results_file is not None` so an empty results file accompanied by `--note` (such as `git diff --quiet`) still uses `prompts/resume-with-results.md` while a note-only resume states that the queued commands were not run.

For container mounts (`--read-dir`), `_resolve_read_dirs()` verifies that each directory exists and `container.overlaps_protected()` refuses paths overlapping the configured sign-in directories (`~/.gemini/antigravity-cli`, `~/.gemini/agy-container-home` or `AGY_CONTAINER_HOME`) or plugin state directory (`state_root()`), including parent directories such as `~` or `~/.claude`. Under Colima's default macOS configuration, only `$HOME` is shared into the virtual machine, so container-mounted directories must sit under `$HOME` rather than `/tmp` or `/var/folders`.

## Claude Code command, agent and prompt rules

- **`allowed-tools` in `commands/*.md` pre-approves matching calls for the turn.** Listing bare `Bash` or `Bash(python3:*)` in commands that handle `needs-approval` queues (`resume.md`, `rescue.md`) pre-approves queued repository commands such as `python3 -m unittest` without prompting the operator. Keep `allowed-tools` narrow (`Read, Write`, or `Bash(git show:*)` for ref verification) where queued commands may be inspected or run.
- **`$ARGUMENTS` expands textually across `commands/*.md` before the model sees the prompt.** Never put literal `$ARGUMENTS` inside `--raw "$ARGUMENTS"` in model-constructed command blocks (`review.md`, `adversarial-review.md`, `plan.md`), because quotes in user arguments break shell quoting. Parse and quote flags individually (`<QUOTED_FLAGS>`) and pass free-form text as one positional argument after `--` (`-- "<ESCAPED_...>"`).
- **`--raw` fallback parsing (`_split_prose_preserving_quotes`).** Inline `!` commands (`status.md`, `result.md`, `apply.md`, `cancel.md`) still use `--raw`, which tries `shlex.split` first and falls back to `_split_prose_preserving_quotes` on `ValueError` (such as an unmatched apostrophe in `caller's`). Any change to that fallback must preserve `#` issue references (`#91`), backslashes (`C:\temp`), inline JavaScript Object Notation (JSON) quotes (`{"id":"1"}`), quoted paths containing spaces, and prose apostrophes.
- **Prompt templates (`plugins/agy/prompts/*.md`).** `template()` renders `{{PLACEHOLDER}}` tokens in a single regex pass so user input containing literal `{{...}}` text is never expanded. Place `<repository_context>` blocks before `<reviewer_instructions>`, `<grounding_rules>` and output contracts. Preserve test-pinned contracts (`cannot-review`, `Cannot review: `, and ``Paths and lines are as of <head>; read a file with `git show <head>:<path>`.``).
