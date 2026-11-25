---
name: agy-review
description: "Use when the user asks agy or Antigravity for a read-only code review, adversarial review, or an update on an existing agy review."
---

From the user's Git checkout, run Python 3 with the installed script at
`../../scripts/agy_codex.py` relative to this SKILL.md's directory. Resolve
that path to an absolute path before invoking it. Keep the checkout as cwd.

- For a review, run `review`; use `adversarial-review` when requested. The
  default scope is automatic. Pass requested `--base`, `--head`, `--scope`,
  `--isolation` and `--model` values individually. For a fetched PR use its
  base and head refs. Pass focus text as a single shell-quoted argument after
  `--`, or use `--instructions-file`. If Codex's sandbox blocks the state directory, Docker socket or sign-in,
  request approval for the specific companion invocation through the usual
  shell approval flow. Preserve any isolation mode the user requested.
- Wait for completion unless the user asks for background execution. For a
  background review, pass `--background` and return the full job ID. For a
  running foreground review, use `result JOB_ID --wait-seconds 120` until
  terminal. Use `status JOB_ID` for progress and `cancel JOB_ID` on request.
- Inspect the reported verdict and findings. Check quoted code, paths and
  lines against the reviewed revision (use the reported commit in `git show <commit>:<path>` for
  an explicit head). Report verified findings first and identify any
  claims you could not verify. Surface an incomplete or failed review.

## Gotchas

Use the full ID returned for this request on every follow-up. Jobs can
outlive a Codex thread. If agy needs approval, report the queued commands
and partial status. This skill supports setup, review, status, result and
cancel. Run `setup --json` to diagnose local agy or container readiness.
