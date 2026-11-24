---
name: agy-writing
description: "Draft or rewrite human-facing prose with agy (Gemini 4) from Claude's raw work-product, diffs, investigation notes or rough drafts: pull request descriptions, GitHub issue or pull request comments, emails, chat posts, changelogs, release notes and technical write-ups."
---

# Writing and rewriting with agy (Gemini 4)

Use this skill when the user wants `agy` (Gemini 4) to turn raw technical work-product into human-facing prose, or to rewrite a draft Claude or the user wrote so it reads naturally.

Claude gathers the verified facts, diffs and constraints on the host; Gemini 4 writes the prose in a read-only `task` job; Claude verifies every factual claim in the returned draft before showing it to the user.

## 1. Build a grounded material packet on the host

Do not paste diffs, stack traces, email threads or rough drafts onto the shell command line, where backticks, `$` variables and quotes break or expand. Do not ask Gemini 4 to run `git log` or `git diff <base>...HEAD` itself: in container mode the job's copy has one baseline commit and no git history or refs.

Instead, create a dedicated leaf directory for this job under `$HOME`, such as `~/.cache/agy-writing/<slug>` (mode `0700`), and write two files inside it with `Write`:

1. `material.md`, holding the reference material structured in flat XML blocks:
   - `<target>`: what is being written (pull request title and body, GitHub issue comment, email reply, chat post, release note, handoff), who will read it (maintainers, engineering peers, non-technical colleague, external vendor), output format (GitHub-Flavored Markdown, plain text, or HyperText Markup Language (HTML)), and hard limits (word budget, flat bullets vs short paragraphs, required sign-off).
   - `<inbound_context>` (when replying to an issue, pull request, email or chat thread): exact quotes of the messages or issue body being answered, so Gemini 4 answers the real question in the right register.
   - `<verified_facts>`: only what was observed or checked in this session:
     - **Code changes (pull requests, commits, release notes):** `git log --oneline <base>..HEAD`, `git diff --stat <base>...HEAD`, `git diff <base>...HEAD` (or working-tree diff), tests run and their exact status, behavioural changes, non-obvious constraints, and any open edge cases.
     - **Investigations, emails and operational updates:** observed facts separated from inferences, exact command or query outcomes, timestamps, and the single action (if any) required from the reader.
   - `<rough_draft>` (when rewriting): the existing text and what is wrong with it (too wordy, artificial tone, buried lead, wrong audience level).
2. `prompt.md`, holding the short instruction for Gemini 4 (read on the host via `--request-file` so nothing touches shell quoting, while `--read-dir` keeps `material.md` out of the initial user prompt):
   ```text
   Read <packet-dir>/material.md and write the requested human-facing text.

   <writing_rules>
   1. Use only facts, paths, identifiers, commands, timestamps and numbers present in material.md or in repository files you open in this session. If a required figure is missing, write [TBD: <what is missing>] rather than inferring it, and keep observed facts distinct from inferences.
   2. Lead with the answer or main change in the first sentence. Write plain, literal, active-voice prose. Cut throat-clearing openings, metaphors, parenthetical clutter, and filler words such as comprehensive, robust, crucial, critical, significant, seamless or elegant.
   3. Match the exact format, length limit and audience register in <target>, expanding unfamiliar acronyms on first use unless the target audience uses them daily.
   4. Do not write or edit any workspace or artifact file, and do not emit file:// links or Antigravity-specific markdown extensions. Return only the finished human-facing text.
   </writing_rules>
   ```

Never put secrets, API keys, tokens or credentials in `material.md`; everything in the job's readable directories goes to Google.

## 2. Pick the Gemini 4 model

- If the user passed `--model`, keep their value unchanged. Never choose `xhigh` on your own: it fails with `INVALID_ARGUMENT` on Neon and is absent on Hydrogen on Gemini Enterprise seats.
- Otherwise pass `--model neon` (`gemini-4-neon-high`). Without `--model`, Sonnet and Haiku callers map to `gemini-4-hydrogen-high`, which is tuned for fast tool loops rather than prose register; Neon has stronger editorial restraint, sentence rhythm and instruction adherence.
- For a short, low-latency rewrite (a brief issue comment or chat reply), `--model neon-medium` halves turnaround time.
- For a complex architectural write-up, postmortem or sensitive stakeholder message where deeper synthesis justifies waiting several minutes, use `--model carbon` (`gemini-4-carbon-medium`).

## 3. Run a read-only `task` job

Locate `agy_companion.py` at `"${CLAUDE_PLUGIN_ROOT}/scripts/agy_companion.py"` when `CLAUDE_PLUGIN_ROOT` is set, or `<skill-base-dir>/../../scripts/agy_companion.py` using the base directory shown when this skill loaded.

Run the companion in the foreground with `--read-only`, `--wait-seconds 590`, `--caller-model "<your model ID>"` (quoted, because IDs such as `claude-opus-5-5[1m]` contain brackets), `--read-dir "<packet-dir>"`, and `--request-file "<packet-dir>/prompt.md"`, setting the Bash tool's `timeout` to `600000`:

```bash
python3 "<plugin-root>/scripts/agy_companion.py" task --read-only --wait-seconds 590 --caller-model "<your model ID>" --model neon --read-dir "<packet-dir>" --request-file "<packet-dir>/prompt.md"
```

Do not add chain-of-thought or effort instructions ("think step by step", "be exhaustive"): Gemini 4's thinking level already allocates reasoning tokens, and extra effort prompts lengthen silent turns toward the idle timeout without improving prose.

If the output says `agy job <id> is still running`, run the printed `result <id> --wait-seconds 590` command (again with `timeout: 600000`) until it finishes.

If a host-mode job finishes with status `needs-approval` (listing queued shell commands), do not treat its partial output as the finished draft and do not delete `<packet-dir>` yet. When a queued command is needed and safe, run it under your normal permission checks, write its output to `<results-file>`, and resume in the foreground with `python3 "<plugin-root>/scripts/agy_companion.py" resume --wait-seconds 590 <id> <results-file>` (`timeout: 600000`, repeating `result <id> --wait-seconds 590` if still running; do not call `/agy:resume`, which detaches with `--background`). When `material.md` already holds everything needed, skip the queued commands with `python3 "<plugin-root>/scripts/agy_companion.py" resume --wait-seconds 590 <id> --note "Write the draft from material.md without running shell commands."` (`timeout: 600000`).

## 4. Verify facts, clean up and hand back

Gemini 4 writes cleaner prose than Claude, while still occasionally smoothing an inference into a stated fact or mistyping a path or identifier. Before presenting the output:

1. Check every file path, `file:line` reference, symbol, command-line interface (CLI) flag, command, version, count and quoted string in Gemini 4's draft against `material.md` and the repository (`Read` or `Grep`).
2. If a factual detail is wrong, correct that detail directly without rewriting Gemini 4's surrounding sentences back into Claude's voice. If the structure or register missed the brief and no other `task` job ran in this session meanwhile (check `/agy:status` if unsure; otherwise start a fresh `task --read-only`), write the short revision note to `<packet-dir>/followup.md` and run one follow-up round with `--resume-last --read-only`:
   ```bash
   python3 "<plugin-root>/scripts/agy_companion.py" task --read-only --resume-last --wait-seconds 590 --read-dir "<packet-dir>" --request-file "<packet-dir>/followup.md"
   ```
3. Delete `<packet-dir>` once no further `--resume-last` round is needed (`trash "<packet-dir>"` on macOS when `trash` is installed, otherwise delete the files inside `<packet-dir>` and `rmdir "<packet-dir>"`).
4. Present the finished draft to the user and stop. Do not open the pull request, post the issue comment, or send the email or chat message unless the user explicitly asked you to send it and your outbound rules permit it.

## Gotchas

- **Use `task --read-only`, never `review`.** `review` and `adversarial-review` pass `--json-schema schemas/review-output.schema.json`, forcing Gemini 4 to emit a JavaScript Object Notation (JSON) verdict and findings array instead of prose. Omitting `--read-only` on `task` puts Gemini 4 in `accept-edits` mode, where it may write the draft to a file in its disposable working copy instead of returning it, and starts an extra export container afterwards.
- **Keep `<packet-dir>` as a dedicated leaf under `$HOME`.** On macOS with Colima, `/tmp` and `/var/folders` are not mounted into containers, so `--read-dir /tmp/...` fails or appears empty inside the container. Conversely, passing a broad parent directory such as `~` or `~/.claude` is refused by `overlaps_protected()` when it contains `~/.gemini/antigravity-cli`, `~/.gemini/agy-container-home` or the plugin's state directory, and would expose unrelated files. A dedicated leaf such as `~/.cache/agy-writing/<slug>` avoids both traps.
- **Running outside a git checkout.** `agy_companion.py` requires a git repository with at least one commit (`HEAD`). When drafting an email or standalone write-up from a scratch directory that is not a git repository, either run the command from the relevant repository checkout, or create a minimal git repository in a scratch directory under `$HOME` (`git init -q` followed by `git commit --quiet --allow-empty -m baseline`) and run from there.
- **Strip `file://` URLs and Antigravity markdown syntax.** Inside `agy`, Gemini 4's own system prompt encourages `file:///absolute/path#L10` links, callout alerts and `carousel` blocks. None of those work in email, chat or external tickets, and `file://` links break on GitHub; keep rule `4` in `prompt.md` and check the output for stray `file://` links before handing over.
