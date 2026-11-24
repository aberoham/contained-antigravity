---
description: Apply the changes from a finished agy task to your checkout
argument-hint: '[job-id] [--check] [--force]'
disable-model-invocation: true
allowed-tools: Bash(python3:*), Bash(git:*)
---

!`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/agy_companion.py" apply --check --raw "$ARGUMENTS"`

If the check reported changes that apply cleanly (not "changed no files") and the user did not pass `--check`, run:
```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/agy_companion.py" apply --raw "$ARGUMENTS"
```
Then show `git diff --stat`. Do not commit.
