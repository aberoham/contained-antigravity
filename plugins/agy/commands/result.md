---
# Adapted from openai/codex-plugin-cc (Apache-2.0) and modified for agy. See NOTICE.
description: Show the stored output of a finished agy job
argument-hint: '[job-id]'
disable-model-invocation: true
allowed-tools: Bash(python3:*), Bash(git:*), Read, Glob, Grep
---

!`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/agy_companion.py" result --raw "$ARGUMENTS"`

If the output says the job is still running, report its status and progress as printed and stop. Otherwise present the output following the `agy-result-handling` skill: keep verdicts, findings, file paths, line numbers, errors and the follow-up commands, and verify quotes and `file:line` claims (using the reported `git show <revision>:<path>` command when the review ran with `--head <ref>`) before relaying them.
