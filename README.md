# Contained Antigravity

Run isolated read-only code reviews and adversarial reviews with Google's Antigravity CLI (`agy`) from Claude Code or local Codex. Both clients support planning, rescue tasks, writing, patch application and approval resumes. Both clients share the same local Python runner and `agy` sign-in. `agy` sends reviewed code to Google.

See [CHANGELOG.md](CHANGELOG.md) for notable changes and upgrade notes.

## Prerequisites

- **Python 3.9+** and **git**
- **agy 1.5.10+** on `PATH` or set via `AGY_BIN`
- A working metered Google Cloud Agent Platform sign-in or an eligible Gemini Enterprise seat (see the [Gemini Enterprise guide](plugins/agy/docs/gemini-enterprise.md)). Run `agy` in a terminal to sign in to your Google Cloud project, then verify with `agy models`.
- **Docker or Colima** (optional) for container isolation. Build the image once from your checkout with `./containers/build.sh`. If your network intercepts TLS, put its root certificates in `containers/certs/` as PEM-format `.crt` files before building. If that directory has none, the script copies `.crt` files directly inside `~/.docker/certs.d`, but not from subfolders. To change them later, replace the files in `containers/certs/` and rebuild. Without a usable container runtime, jobs default to host isolation. Passing `--isolation container` requires Docker and fails if it is unavailable. On Linux hosts using a system keyring for `agy`, run `./containers/signin.sh` from the clone to create a container-only sign-in.

## Installation

### Claude Code

Add the marketplace and install:

```text
/plugin marketplace add aberoham/contained-antigravity
/plugin install agy@contained-antigravity
```

For a local branch preview, substitute `/plugin marketplace add ./`. Run `/agy:setup` to verify your setup, then try `/agy:review`. Update existing installs with `/plugin marketplace update contained-antigravity` and the `/plugin` menu.

### Codex CLI

Install directly from GitHub without cloning the repository:

```bash
codex plugin marketplace add aberoham/contained-antigravity
codex plugin add agy@contained-antigravity
```

This installs the `agy:agy-review` skill. If you want to build the optional container image, run the build from a source checkout or the downloaded marketplace root.

For plugin development from a local checkout, add the repository root as a local marketplace source instead:

```bash
codex plugin marketplace add .
codex plugin add agy@contained-antigravity
```

Open a new thread in your target repository and ask, `"Use agy to review this branch against main"`. Codex invokes the `agy:agy-review` skill and prompts for shell approval as usual. Use the full job ID for status, results, or cancellation. Jobs continue after thread closure, store state in `~/.cache/agy-codex/state`, and accept `--isolation container`.

## Migrating from `agy-cc`

Run this once if you previously installed from the bundled `agy-cc` marketplace. Skip this section for fresh installs or unrelated external marketplaces (which keep their own ID).

Codex reads the new catalog name when its marketplace refreshes and then cannot find `agy@agy-cc`, so Codex installs must migrate. Claude Code keeps the name it recorded when you added the marketplace, so an existing `agy@agy-cc` install keeps updating. There, migrating is optional, and the new install does not see the old install's jobs.

1. Finish any active jobs. In Claude Code, resolve any `needs-approval` jobs, apply any patches you want to keep with `/agy:apply`, and save needed results before uninstalling `agy@agy-cc`.
2. Remove the old plugin and marketplace entry for your client.

   **Claude Code (terminal)**

   ```bash
   claude plugin uninstall agy@agy-cc --keep-data
   claude plugin marketplace remove agy-cc
   ```
   `--keep-data` preserves persistent data under the old plugin ID. Reinstalling does not transfer it to `agy@contained-antigravity`.

   **Codex**

   ```bash
   codex plugin remove agy@agy-cc
   codex plugin marketplace remove agy-cc
   ```

3. Follow the install steps above to add `aberoham/contained-antigravity` and install `agy@contained-antigravity`.
4. Update any team configs that reference `agy-cc` or `agy@agy-cc`.

## Use

**Claude Code:** use the slash commands below.

In Codex, ask “Use agy to plan the retry change”, “Use agy to fix the failing
test in its own working copy”, or “Use agy to rewrite this pull request
description”. Writing runs read-only. To bring a rescue patch into your
checkout, ask “Apply agy job <full-job-id>”. For a job waiting on approval,
ask “Resume agy job <full-job-id> with these results” or supply a note
explaining which commands to skip. Use the same full ID in a new thread.

| Command | What it does |
| --- | --- |
| `/agy:review` | Reviews your uncommitted changes, or, on a clean checkout, the branch against the default branch. |
| `/agy:review --scope working-tree` | Reviews only the uncommitted changes. |
| `/agy:review --base main` | Reviews the current branch against `main`. |
| `/agy:review --base main --head <ref>` | Reviews another ref, such as a fetched pull request, without checking it out. |
| `/agy:adversarial-review [focus]` | A review that challenges the design and its assumptions. |
| `/agy:plan <request>` | Writes an implementation plan. |
| `/agy:rescue <request>` | Hands a debugging or implementation task to agy. agy edits its own copy of the repository. |
| `/agy:agy-writing <request>` | Drafts or rewrites prose such as pull request descriptions, comments and release notes. |
| `/agy:status [job]` | Lists jobs, or shows one job's rounds, stalls and token use. |
| `/agy:result [job]` | Shows a finished job's output. |
| `/agy:apply [job]` | Applies a finished task's changes to your checkout. `--check` tests first. |
| `/agy:resume <job>` | Sends the output of commands agy queued back to agy (host mode). |
| `/agy:cancel [job]` | Stops a running job. |
| `/agy:setup` | Checks that agy is installed and signed in, and which isolation jobs will use. |

You can also ask Claude in plain words, for example "get agy to review this
branch", "ask agy why this test fails" or "have agy write the pull request
description for this change". Claude starts code and review jobs through the
plugin's `agy-rescue` subagent, drafts human-facing prose through the
`agy-writing` skill, and checks agy's quotes and `file:line` claims before
relaying them.

Common options, written for Claude Code. In Codex, name the one you want in
your request:

- `--model` picks the Gemini model: a thinking level (`low`, `medium`,
  `high`, `xhigh`), a family (`neon`), both (`neon-high`), or a full ID from
  `agy models`. Without it, a new job gets the Gemini model mapped to the
  Claude model you are running (see the table below). If it cannot tell, it
  uses the model set in agy itself, and with none set agy picks. A resumed
  job keeps its model. A Gemini 4 family on its own gets Google's default
  level for it (High for Neon and Hydrogen, Medium for Argon and Carbon);
  any other name gets Medium. A level on its own keeps the family of that
  model, or uses Neon.
- `--background` returns at once. Check on the job with `/agy:status`.
- `--isolation container|host` overrides the isolation that `/agy:setup`
  reported.
- `--read-dir <path>` lets agy read one more directory, such as a sibling
  repository. Repeat it for several. What agy reads there goes to Google too.
  In Codex, name each directory you want agy to read in your request.
- Reviews also take instructions on what to check: text after the flags, or
  `--instructions-file <path>`.

Reviews and plans are read-only. A task's changes stay in the job's copy
until you run `/agy:apply`, which refuses if the same files changed in your
checkout meanwhile.

### Which Gemini model to pick

`--model` also takes any full model ID that `agy models` lists, such as
`gemini-4-neon-medium`.

**What Google says each model is for.** The names are chemical elements
and do not show an order.

| Model | Google's description | Thinking levels (Google's default in bold) |
| --- | --- | --- |
| Neon | Complex coding, analytical reasoning and production enterprise workflows; frontier reasoning at speed | Low, Medium, **High**, XHigh |
| Hydrogen | Google's default general-purpose model, for speed and everyday work: coding, tool-using agents, parallel subagents | Low, Medium, **High**, XHigh |
| Argon | An efficient reasoning model: Gemini 4 intelligence with faster responses and fewer tokens per turn than Carbon | Low, **Medium**, High |
| Carbon | Google's strongest reasoning model, for the hardest long-running work: architecture, root-cause debugging, security audits. It spends more thinking per turn | Low, **Medium**, High |

On a Gemini Enterprise seat on 20 November 2026, `agy models` listed no
XHigh for Hydrogen, and Neon XHigh failed with `INVALID_ARGUMENT`.

**The plugin's defaults.** Without `--model`, a new job maps the calling
Claude model to a Gemini model at Google's default level: Haiku and Sonnet
get `gemini-4-hydrogen-high`, Opus and Fable `gemini-4-neon-high`. The
mapping is our own choice, from the scores and review times below, not a
claim by Google or Anthropic.

| Gemini family | Nearest Claude | Scores | A review here took | Use it for |
| --- | --- | --- | --- | --- |
| Hydrogen | Sonnet 5.5 | Index 51 at High. Sonnet scores 42 at high, 50 at XHigh. | about 1 min at Medium, 2 at High | Quick second opinions and most reviews |
| Neon | Opus 5.5 | Index 55 at Medium, 60 at High. Opus scores 52 at high, 57 at XHigh. | about 2 min at Medium, 5 at High | Reviews where depth matters more than waiting |
| Carbon | Between Opus 5.5 and Fable 5.2 | Index 60 at High; Fable scores 62 at High and XHigh. | median 5 to 10 min, longest 13 min 20 s | Hard, long-running tasks in the background |
| Argon | Opus 5.5 at medium | Index 48 at Medium, 50 at High; Opus scores 48 at medium. | about 1 min at Medium, 5 at High | Quick answers at lower token cost |

- Hydrogen High scores 51 on the index, against 36 for Haiku 5.5 at Max.
- No Gemini 4 model reaches Fable 5.2 at Max (65).
- "Index" is the Artificial Analysis Intelligence Index v5, read on
  20 November 2026. Review times are from this plugin on one seat and
  measure speed only, not review quality; the detail is in
  [NOTES.md](NOTES.md).

**Carbon and Argon are opt-in.** Google reserves Carbon for the hardest
work, and it is the slowest model here. Use `--model carbon` with
`--background` when a hard task is worth a wait of ten minutes or more.
Argon scores below Neon and Hydrogen at their default levels; use
`--model argon` when speed and token cost matter more.

**Long turns.** Since agy 1.5.10, agy waits 10 minutes, not 5, before it
treats a silent model as stalled and retries the turn, so a model can
think for up to 10 minutes without output. That is why the plugin needs
1.5.10 or later. A turn that stays silent longer is still cancelled and
retried, and Carbon and Argon still stall at times (agy issue
[1402](https://github.com/google-antigravity/antigravity-cli/issues/1402)).

Sources:

- Google's model guides, such as
  https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/guides/gemini-4-neon
  and https://aistudio.google.com/docs/thinking
- Google's model cards, such as
  https://deepmind.google/models/model-cards/gemini-4-neon/
- https://antigravity.google/docs/models/
- Artificial Analysis model pages, such as
  https://artificialanalysis.ai/models/gemini-4-neon and
  https://artificialanalysis.ai/models/claude-opus-5-5

## How agy is contained

- **Container mode** (Docker or Colima): `agy` runs inside a Linux container with access only to the job's repository copy, mounted job data, plugin files, and `--read-dir` paths. The host home and Keychain stay unmounted. Each job gets a `0600` token copy and project settings in its private `HOME` until cleanup.
- **Host mode** (no container): agy runs in a git worktree under a tool
  policy hook and, on macOS, a `sandbox-exec` write sandbox. Shell commands
  outside a short read-only allow-list are queued for Claude, which runs
  them under your normal Claude Code permission checks.
- In both modes agy still reaches Google over the network, and the
  repository content goes to Google for the review.

The [plugin README](plugins/agy/README.md) has the full detail: the
containment model and its limits, stall handling, failure classes and exit
codes, and known problems.

## More

- [`plugins/agy/README.md`](plugins/agy/README.md): full reference for the
  plugin.
- [`plugins/agy/docs/gemini-enterprise.md`](plugins/agy/docs/gemini-enterprise.md):
  what an organisation needs before agy works with a Gemini Enterprise
  licence.
- [`NOTES.md`](NOTES.md): how agy was observed to behave, and open
  questions.
- [`containers/`](containers): the image and the sign-in script. The design
  follows Matt Pocock's [Sandcastle](https://github.com/mattpocock/sandcastle).

The repository was called `aberoham/agy-cc`, then
`aberoham/contained-antigravity-for-claude-code`, until November 2026. GitHub
redirects the old names.

## Development

Try a local checkout with `claude --plugin-dir plugins/agy`.

Checks, matching CI:

```
uvx ruff@0.16.6 check plugins tests
uvx ruff@0.16.6 format --check plugins tests
uvx --python 3.13 --with pytest==9.1.1 ty@0.0.78 check plugins tests
uv run --no-project --python 3.9 --with 'pytest>=8.4,<10' pytest -q
```

Before a release, run `scripts/live-smoke.sh --apply` on a machine where agy
is signed in. It checks the companion's commands against the real agy.

## Licence

Apache License 2.0. See [NOTICE](NOTICE) for the parts adapted from OpenAI's
codex-plugin-cc.
