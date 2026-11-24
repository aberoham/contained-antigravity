---
# Adapted from openai/codex-plugin-cc (Apache-2.0) and modified for agy. See NOTICE.
name: gemini-prompting
description: Guidance for turning a user's request into a clear prompt for agy (Gemini 4) in the agy-rescue subagent
user-invocable: false
---

# Prompting agy (Gemini 4)

Use this skill only to structure the request text that `agy-rescue` forwards to `task`. Do not inspect the repository or solve the task.

The companion already wraps `task` in `prompts/task.md` (telling Gemini 4 whether the copy is writable or read-only, how commands work, to return `Cannot review:` when material is missing, and how to report). Add structure only where the caller's request is ambiguous:

- **One task per job.** Split unrelated asks.
- **Separate context from the directive with flat XML tags.** When a request mixes background narrative with instructions, Gemini 4 treats narrative details as extra requirements. Organise multi-part asks into `<context>`, `<goal>`, `<constraints>` and `<done_when>` blocks while keeping every user fact, identifier, path and diff intact.
- **Done condition.** State what finished looks like ("the failing test in `tests/test_upload.py` passes", not "fix uploads").
- **Scope.** Name the entry-point files or directories in play when the caller named them, and state what must not change. Giving concrete entry-point paths lets Gemini 4 batch its initial file reads instead of spending extra turns searching.
- **Evidence.** For diagnosis or architecture questions, pass `--read-only` and ask Gemini 4 to separate what it observed in opened files from what it infers.
- **Material.** agy sees only the repository's files, and in container mode no git history or refs. A `task` request that depends on a diff between refs, a pull request description or an external file must carry that text (written to a file passed via `--request-file <path>`, or placed in a leaf directory under `$HOME` passed via `--read-dir`). Never write "run `git diff origin/main...pr/96`"; for a review of a ref the subagent uses `review --base <ref> --head <ref>`.
- **No verdict without the material.** For a judgement on pasted material, add: "If anything you need is missing, say what and give no verdict."
- **Exact copies.** If the user wants text copied from a file, say "open `<path>` in this session and copy it character for character, or say you could not open it".

Keep the user's wording where it is already clear. Never add chain-of-thought or effort instructions ("think step by step", "be thorough", "check everything"): Gemini 4's thinking level already allocates latent reasoning tokens, and extra effort prompts lengthen silent turns toward `agy`'s 10-minute idle timeout. Do not raise `--model` unless the user asked, and never choose `xhigh`.

## Gotchas

- Pass prepared `task` text via `--request-file <path>` (and review context via `--instructions-file <path>`) rather than quoting multiline text on the shell command line, where markdown backticks and `$` variables expand in double quotes.
- In host mode agy cannot run tests itself: a test command is queued for Claude and costs a resume round. If the done condition needs a test run, name the exact command so it is queued once rather than guessed at, and let Gemini 4 use its native file tools (`read_file`, `grep_search`, `find_by_name`, `list_dir`) for code inspection. In container mode agy runs its own commands.
- Long pasted context in `--request-file` is re-sent on every `--resume-last` round because a resumed conversation carries its history. Point agy at files in the repository instead of pasting them, or put large external reference material in a dedicated leaf directory under `$HOME` via `--read-dir`.
- "Verbatim" in a prompt does not guarantee an exact copy; Gemini 4 can reconstruct text from its reasoning trace after long tool loops. The `agy-result-handling` verification step catches invented quotes and line numbers.
