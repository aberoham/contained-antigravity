---
# Adapted from openai/codex-plugin-cc (Apache-2.0) and modified for agy. See NOTICE.
description: Hand a debugging, implementation or second-opinion task to agy (Gemini 4) through the agy-rescue subagent
argument-hint: "[--background|--wait] [--resume|--fresh] [--read-only] [--read-dir <directory>] [--isolation container|host] [--model <level|family|family-level|model ID>] [--request-file <path>] [what agy should investigate, do or review]"
allowed-tools: Read, Glob, Grep, Write, Bash(git show:*), AskUserQuestion, Agent
---

Invoke the `agy:agy-rescue` subagent with the `Agent` tool (`subagent_type: "agy:agy-rescue"`), forwarding the raw user request as the prompt. It is a subagent, not a skill: do not call `Skill(agy:agy-rescue)` or `Skill(agy:rescue)`.

Raw user request:
$ARGUMENTS

Execution mode:
- `--background` runs the subagent in the background; `--wait` or neither runs it in the foreground.
- `--resume` means continue the last agy task in this repository; `--fresh` means start a new one. Without either, if the request is clearly a follow-up ("continue", "keep going", "apply the top fix"), use `--resume` only when the previous agy task is compatible (a job started with `--read-only` cannot edit files later, so an edit follow-up after a read-only diagnosis starts `--fresh` with the chosen fix included); otherwise start fresh.
- Keep `--model`, `--isolation`, `--read-only`, `--read-dir` and `--request-file` in the forwarded request. Do not invent `--model xhigh` when the user asks for deeper thinking (`xhigh` fails on Neon and is absent on Hydrogen on Gemini Enterprise; use `--model neon` or `--model carbon` only if the user asked to raise the model).
- Add `--caller-model "<your model ID>"` (quoted) to the forwarded request, with the Claude model ID you are running as, such as `claude-opus-5-5`. For a new job without `--model`, the companion uses it to pick the matching Gemini model; a resumed job keeps its model.
- A request to review a branch, a ref or a fetched pull request is forwarded the same way; the subagent runs the companion's `review` command for it, with `--base` and `--head`.

If no request was given, ask what agy should investigate or fix.

Present the subagent's output following the `agy-result-handling` skill: verify `file:line` references and quoted excerpts before relaying them (against the user's repository for read-only jobs, the reported `git show <revision>:<path>` command for a `--head` review, or the job's working copy for files a writable task edited), report `Cannot review:` results as no review, point to `/agy:apply <job>` only when a writable task actually changed files, and if the job ends in `needs-approval`, follow the "Queued commands" section.
