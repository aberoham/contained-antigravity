---
description: Send the output of commands agy queued back to its job and let it continue
argument-hint: '<job-id> [results-file] [--note <text>] [--model <level|family|family-level|model ID>]'
disable-model-invocation: true
allowed-tools: Read, Write
---

Resume an agy job that ended in `needs-approval`.

Raw arguments:
`$ARGUMENTS`

If no results file was given:
1. Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/agy_companion.py" status <job-id>` to list the queued commands and the job's working copy.
2. Run each queued command you would run yourself against that working copy, using your own Bash tool, one command per call (prefer tool flags that accept the working copy path over `cd <worktree> && ...` chains). Your normal permission checks apply. Skip any command you would not run, and say why in the note.
3. Write each command, its exit status and its relevant output to a file in your scratchpad. If a command prints thousands of passing lines, keep the summary, exit status and any failure output rather than pasting the entire log: a resumed Gemini 4 conversation resends its history on every later turn.

Then run:
```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/agy_companion.py" resume --background <job-id> <results-file> --note "<what you skipped and why, if anything>"
```
Add `--model <value>` only when the arguments include it; without it the job keeps its model.
Tell the user the job is resuming and to check `/agy:status <job-id>`.
