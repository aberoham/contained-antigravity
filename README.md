# Contained Antigravity for Claude Code

A Claude Code plugin that hands work to Google's Antigravity CLI (`agy`,
Gemini 4): code reviews, adversarial reviews, implementation plans and
debugging or implementation tasks. Each one runs as a tracked background job
on its own copy of your repository, inside a container when Docker is
available. agy never edits your checkout and never runs with `--yolo` on your
workstation.

## What you need

- **agy 1.1.12 or later**, installed and on `PATH`
  (https://antigravity.google/docs/cli), or `AGY_BIN` set to its path.
- **A Gemini Enterprise seat**, and access to the Google Cloud project that
  agy is configured to use. Sign in once by running `agy` in a terminal
  window. The sign-in is interactive, so it cannot run inside Claude Code.
- **git** and **python3** 3.9 or later. The macOS system Python is enough.
- **Docker or Colima** for container isolation. This is optional but
  recommended. Without it, jobs run in host mode, described below.

## Install

1. In Claude Code, add this repository as a marketplace and install the
   plugin:

   ```
   /plugin marketplace add aberoham/contained-antigravity-for-claude-code
   /plugin install agy@agy-cc
   ```

   If Claude Code asks you to reload plugins or restart, do that before the
   next step.

2. Build the container image in a terminal. Start Docker Desktop, or run
   `colima start`, first. The marketplace step already downloaded the
   script:

   ```
   ~/.claude/plugins/marketplaces/agy-cc/containers/build.sh
   ```

   It builds the image for your user, and on later runs restarts Colima if
   it has stopped. Container jobs reuse your ordinary `agy` sign-in, from
   `~/.gemini/antigravity-cli/antigravity-oauth-token` or, on macOS, from
   the Keychain. On a Linux host whose agy keeps its sign-in in the system
   keyring, run `~/.claude/plugins/marketplaces/agy-cc/containers/signin.sh`
   once in a terminal window to make a container-only sign-in. Skip this
   step if you only want host mode.

3. In Claude Code, run `/agy:setup`. It checks that agy is installed and
   signed in, shows the Google Cloud project agy will use, and says whether
   jobs will run in a container or on the host.

To update later, run `/plugin marketplace update agy-cc` to refresh the
listing, then update the agy plugin from the `/plugin` menu. Run `build.sh`
again if the image is ever deleted, for example by `docker system prune`.

## Use

| Command | What it does |
| --- | --- |
| `/agy:review` | Reviews your uncommitted changes, or, on a clean checkout, the branch against the default branch. |
| `/agy:review --scope working-tree` | Reviews only the uncommitted changes. |
| `/agy:review --base main` | Reviews the current branch against `main`. |
| `/agy:review --base main --head <ref>` | Reviews another ref, such as a fetched pull request, without checking it out. |
| `/agy:adversarial-review [focus]` | A review that challenges the design and its assumptions. |
| `/agy:plan <request>` | Writes an implementation plan. |
| `/agy:rescue <request>` | Hands a debugging or implementation task to agy. agy edits its own copy of the repository. |
| `/agy:status [job]` | Lists jobs, or shows one job's rounds, stalls and token use. |
| `/agy:result [job]` | Shows a finished job's output. |
| `/agy:apply [job]` | Applies a finished task's changes to your checkout. `--check` tests first. |
| `/agy:resume <job>` | Sends the output of commands agy queued back to agy (host mode). |
| `/agy:cancel [job]` | Stops a running job. |

You can also ask Claude in plain words, for example "get agy to review this
branch" or "ask agy why this test fails". Claude starts the job through the
plugin's `agy-rescue` subagent and checks agy's quotes and `file:line`
claims before relaying them.

Common options:

- `--model` picks the Gemini model: a thinking level (`low`, `medium`,
  `high`, `xhigh`), a family (`neon`), both (`neon-high`), or a full ID from
  `agy models`. Without it, a new job gets the Gemini model mapped to the
  Claude model you are running (see the table below). If it cannot tell, it
  uses the model set in agy itself, and with none set agy picks. A resumed
  job keeps its model. A level on its own keeps the family of that model,
  or uses Neon.
- `--background` returns at once. Check on the job with `/agy:status`.
- `--isolation container|host` overrides the isolation that `/agy:setup`
  reported.

Reviews and plans are read-only. A task's changes stay in the job's copy
until you run `/agy:apply`, which refuses if the same files changed in your
checkout meanwhile.

### Which Gemini model to pick

`--model` also takes any full model ID that `agy models` lists, such as
`gemini-4-neon-medium`. Each Gemini 4 family has Low, Medium and High
thinking levels. Neon also lists XHigh, but on a Gemini Enterprise seat on
20 November 2026 it failed with `INVALID_ARGUMENT`.

**Gemini 4 against Claude.** This is our own assessment from the sources
below, not a claim by Google or Anthropic. Without `--model`, a new job uses
it: Haiku gets `gemini-4-hydrogen-medium`, Sonnet `gemini-4-hydrogen-high`,
Opus `gemini-4-neon-medium` and Fable `gemini-4-neon-high`. These are the
plugin's choices, made for a score near Claude's usual effort and a review
time short enough to wait for.

| Gemini family | Nearest Claude | Why | A review here took | Use it for |
| --- | --- | --- | --- | --- |
| Hydrogen | Sonnet 5.5 | Index 51 at high, and 54 at XHigh, a level this seat does not offer. Sonnet scores 42 at high, 50 at XHigh. Google benchmarks it against Sonnet. | about 1 min at medium, 2 at high | Quick second opinions and most reviews |
| Neon | Opus 5.5 | Index 55 at medium, 60 at high. Opus scores 52 at high, 57 at XHigh. Google benchmarks it against Opus. | about 2 min at medium, 5 at high | Reviews where depth matters more than waiting |
| Carbon | Between Opus 5.5 and Fable 5.2 | Index 60 at high; Fable scores 62 at High and XHigh. About 50 to 60 tokens a second, the slowest output with Argon. | median 5 to 10 min over earlier reviews, longest 13 min 20 s | Long, hard tasks in the background |
| Argon | Opus 5.5 at medium | Index 48 at medium, 50 at high; Opus scores 48 at medium. | about 1 min at medium, 5 at high | Comparing with reviews from before this change |

- Hydrogen Medium scores higher than Haiku 5.5 at Max (41 against 36) at
  about 15 times Haiku's price per token.
- No Gemini 4 model reaches Fable 5.2 at Max (65).
- "Index" is the Artificial Analysis Intelligence Index v5, read on
  20 November 2026. Review times are from this plugin on one seat and
  measure speed only, not review quality; the detail is in
  [NOTES.md](NOTES.md).

**Why Carbon and Argon are not defaults.** Both still work with `--model`.

- **Carbon** is the model Google calls its strongest, for long, multi-step
  work and security analysis. It scores like Neon High and takes longer.
  Use `--model carbon` with `--background` when a hard task is worth a
  wait of ten minutes or more.
- **Argon** came to Antigravity on 13 October 2026 and was the plugin's
  default until now. Argon Medium answers quickly and scores like
  Opus at medium, but Neon and Hydrogen score higher at their high levels.
  Use `--model argon` to compare with an earlier review.

The names are chemical elements and do not show an order. Carbon is the
strongest and Hydrogen the fastest, but Argon has the highest atomic number.
The posts we read describe each model's role; the ranking in the table is
ours.

Sources:

- Artificial Analysis model pages, such as
  https://artificialanalysis.ai/models/gemini-4-neon and
  https://artificialanalysis.ai/models/claude-opus-5-5
- Google's model cards, such as
  https://deepmind.google/models/model-cards/gemini-4-neon/
- https://antigravity.google/docs/models/

## How agy is contained

- **Container mode** (Docker or Colima, and the image): agy runs in a Linux
  container that sees only the job's copy of the repository, the job's own
  state, the plugin's files and any directory you pass with `--read-dir`. Your home directory, `~/.ssh`, `~/.aws` and
  keychain are not mounted. Only a patch comes back to the host.
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

The repository was called `aberoham/agy-cc` until November 2026. GitHub
redirects the old name, and the marketplace keeps the name `agy-cc`, so an
existing install needs no change.

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
