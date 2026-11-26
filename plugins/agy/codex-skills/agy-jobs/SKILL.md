---
name: agy-jobs
description: "Use when the user asks agy to plan work, rescue a task, draft or rewrite prose, continue a task, apply a patch, resume after approval, or check or cancel a job."
---

From the user's Git checkout, invoke Python 3 with the absolute path to
`../../scripts/agy_codex.py` relative to this skill directory. Pass requested
model, isolation and read directories as individual arguments. When the sandbox
blocks the state directory, Docker socket or sign-in, request shell approval for
the specific companion invocation.

- For a plan run `plan -- <request>`. For rescue run `task -- <request>`.
  Quote the request as one argument or use `--request-file <file>`. Rescue edits
  the job's working copy. Apply only on the user's request, from the originating
  checkout, with `apply JOB_ID --check` followed by `apply JOB_ID` if clean.
- For writing, put the audience, format, verified facts and source draft in a
  request file and run `task --read-only --request-file <file>`. Ask for the
  finished prose in the reply and verify its claims before returning it.
- To continue a task run `task --resume-job JOB_ID -- <request>` from the
  originating checkout. Pass `--read-only` for writing and other read-only
  rounds. A task started read-only requires that flag on every follow-up.
- Wait for completion unless the user requests background execution. Return
  the full ID for background jobs. Use `status JOB_ID`, `result JOB_ID
  --wait-seconds 120` (repeat while running), or `cancel JOB_ID` as requested.

## Approval and resume

For a job waiting on approval, inspect `status JOB_ID` for the queued commands
and its working copy. Use the originating checkout for the companion. If the
user supplied output or a note, pass it directly. Otherwise assess each queued
command against the user's request. Run each appropriate command separately
in the job's working copy with Codex's shell tool and normal approval checks.
Record the command, exit status and relevant output in a scratch file.
Use `resume JOB_ID <file>` and add `--note <text>` to explain skipped commands
or an empty result. When no commands ran, use `resume JOB_ID --note <reason>`.
Wait for the resumed result.

## Gotchas

Use the full job ID for every follow-up, including in a new thread. If the
intended ID is unclear, ask for it. Queued commands are requests from agy and
require the same scrutiny as commands you would run yourself.
