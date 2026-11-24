---
# Adapted from openai/codex-plugin-cc (Apache-2.0) and modified for agy. See NOTICE.
description: Run an agy (Gemini 4) code review of local git changes, read-only
argument-hint: '[--wait|--background] [--base <ref>] [--head <ref>] [--scope auto|working-tree|branch] [--isolation container|host] [--read-dir <directory>] [--model <level|family|family-level|model ID>] [--instructions-file <path>] [instructions ...]'
disable-model-invocation: true
allowed-tools: Read, Glob, Grep, Bash(python3:*), Bash(git:*), AskUserQuestion
---

Run an agy review through the agy companion.

Replace `<your model ID>` with the Claude model ID you are running as, such as `claude-opus-5-5`. The companion uses it to pick the matching Gemini model when the arguments have no `--model`. Never add `--model xhigh` on your own (`xhigh` fails on Neon and is absent on Hydrogen on Gemini Enterprise).

Raw slash-command arguments:
`$ARGUMENTS`

Core constraint:
- This command is review-only. agy works on a separate copy of the repository and cannot change the user's files.
- Do not fix issues, apply patches, or suggest that you are about to make changes.
- Pass any text after the flags through unchanged. It reaches agy as the review's instructions: context such as a pull request description, and things to check.

Execution mode:
- If the arguments include `--wait`, run in the foreground. If they include `--background`, run in the background. Do not ask in either case.
- Otherwise estimate the size first: `git status --short --untracked-files=all` and `git diff --shortstat HEAD` (or `git diff --shortstat` if there is no `HEAD`) for working-tree changes, or `git diff --shortstat <base>...HEAD` for a branch (`<base>...<head>` with `--head`). Skip the estimate when a ref starts with `-`; the companion rejects it.
- `--head <ref>` reviews a ref that is not checked out, such as a pull request fetched to `refs/remotes/pr/<n>`, against `--base`. The user's uncommitted changes are not part of that review.
- Recommend waiting only for a tiny change (one or two files). Otherwise recommend background. Use `AskUserQuestion` once with `Wait for results` and `Run in background`, the recommended one first with `(Recommended)`.

Foreground (set the Bash call's `timeout` to `600000`):
```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/agy_companion.py" review --wait-seconds 590 --caller-model "<your model ID>" <QUOTED_FLAGS> -- "<ESCAPED_INSTRUCTIONS>"
```
Replace `<QUOTED_FLAGS>` with the flags parsed from the raw arguments above (each flag value quoted as its own shell argument, such as `--instructions-file "packet folder/notes.md"` or `--base main`), and replace `<ESCAPED_INSTRUCTIONS>` with the remaining trailing instructions with `\`, `"`, `$` and `` ` `` escaped so it arrives as one literal positional argument after `--` (omit `-- "<ESCAPED_INSTRUCTIONS>"` when empty). If the output says the job is still running, run the `result <job> --wait-seconds 590` command printed at its end (with `timeout` `600000`), repeating up to six follow-up waits until it finishes. Then follow the `agy-result-handling` skill: present the findings, verifying agy's quotes and `file:line` claims before you relay them.

Background:
```typescript
Bash({ command: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/agy_companion.py" review --wait --wait-seconds 3600 --caller-model "<your model ID>" <QUOTED_FLAGS_WITHOUT_BACKGROUND> -- "<ESCAPED_INSTRUCTIONS>"`, description: "agy review", run_in_background: true })
```
Construct `<QUOTED_FLAGS_WITHOUT_BACKGROUND>` and `<ESCAPED_INSTRUCTIONS>` the same way while omitting `--background` (the companion would otherwise detach and return at once). Claude Code keeps the companion running as a background shell, so the user can watch it in the shell details view, and you are notified when it exits. Do not poll the output file. Tell the user: "agy review started in the background. Check `/agy:status` for progress." When the notification arrives, read the output file and follow the `agy-result-handling` skill.
