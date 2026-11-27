---
description: Ask agy (Gemini 4) to write an implementation plan without changing anything
argument-hint: '[--wait|--background] [--isolation container|host] [--read-dir <directory>] [--web] [--model <level|family|family-level|model ID>] [--request-file <path>] <what the plan is for>'
disable-model-invocation: true
allowed-tools: Read, Glob, Grep, Bash(python3:*), AskUserQuestion
---

Ask agy for an implementation plan. agy runs read-only on a separate copy of the repository and writes the plan as its own artifact.

Replace `<your model ID>` with the Claude model ID you are running as, such as `claude-opus-5-5`. The companion uses it to pick the matching Gemini model when the arguments have no `--model` (never add `--model xhigh` on your own).

Raw slash-command arguments:
`$ARGUMENTS`

Rules:
- If no request text and no `--request-file` was given, ask what the plan should cover and stop.
- Plans usually take several minutes. Unless `--wait` is present, run in the background so Claude Code notifies you when the plan finishes.
- Do not start implementing the plan. Present it and stop.

Foreground (when `--wait` is present; set the Bash call's `timeout` to `600000`):
```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/agy_companion.py" plan --wait-seconds 590 --caller-model "<your model ID>" <QUOTED_FLAGS> -- "<ESCAPED_REQUEST>"
```
Replace `<QUOTED_FLAGS>` with the flags parsed from the raw arguments above (each flag value quoted as its own shell argument, such as `--request-file "packet folder/request.md"` or `--read-dir "my docs"`), and replace `<ESCAPED_REQUEST>` with the remaining request text with `\`, `"`, `$` and `` ` `` escaped so it arrives as one literal positional argument after `--` (omit `-- "<ESCAPED_REQUEST>"` when `--request-file` carries the whole request). If the output says the job is still running, run the `result <job> --wait-seconds 590` command printed at its end (with `timeout` `600000`), repeating up to six follow-up waits until it finishes.

Background (default when `--wait` is absent):
```typescript
Bash({ command: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/agy_companion.py" plan --wait --wait-seconds 3600 --caller-model "<your model ID>" <QUOTED_FLAGS_WITHOUT_BACKGROUND> -- "<ESCAPED_REQUEST>"`, description: "agy plan", run_in_background: true })
```
Construct `<QUOTED_FLAGS_WITHOUT_BACKGROUND>` and `<ESCAPED_REQUEST>` the same way while omitting `--background` so the companion stays attached to the background shell until the job finishes. Do not poll the output file. Tell the user: "agy plan started in the background. Check `/agy:status` for progress."

When the plan finishes (in the foreground or via the background notification), follow the `agy-result-handling` skill. Plans are where agy has invented file contents before: check every file, function and quoted text it names against the repository, and say which you checked.
