# Contained Antigravity Plugin

Run isolated read-only and adversarial code reviews with Google's Antigravity CLI (`agy`) in Claude Code or local Codex. Claude Code also plans, rescues, and applies patches. Both clients share one local Python runner and your `agy` sign-in. `agy` sends reviewed code to Google.

## Prerequisites

- **Python 3.9+**, **git**, and **agy 1.5.10+** on `PATH` or `AGY_BIN`
- A metered Google Cloud Agent Platform sign-in or an eligible Gemini Enterprise seat (see [docs/gemini-enterprise.md](docs/gemini-enterprise.md)). Sign in with `agy` in a terminal and check `agy models`.
- **Docker or Colima** (optional) for container isolation. Build once from a checkout with `./containers/build.sh`. Without Docker, jobs default to host mode and `--isolation container` fails. Linux keyring users run `./containers/signin.sh` for container sign-in.

## Install

- **Claude Code:** Run `/plugin marketplace add aberoham/contained-antigravity` (or `/plugin marketplace add ./` locally), then `/plugin install agy@contained-antigravity` and `/agy:setup`. Update via `/plugin marketplace update contained-antigravity` and `/plugin`.
- **Codex CLI:** Run `codex plugin marketplace add aberoham/contained-antigravity` then `codex plugin add agy@contained-antigravity` to install `agy:agy-review` without cloning (for local plugin development, run `codex plugin marketplace add .` from a checkout instead).

From another marketplace that lists this plugin:

```
/plugin install agy@<marketplace>
```

The repository was called `aberoham/agy-cc`, then
`aberoham/contained-antigravity-for-claude-code`, until November 2026. GitHub
redirects the old names.

To try a local checkout: `claude --plugin-dir plugins/agy`.

Then run `/agy:setup`.

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

## Commands

Use the slash commands below in Claude Code. In Codex, ask for a review in plain
language, for example "use agy to review this branch against main". The
`agy:agy-review` skill also checks setup and shows results, reports progress or
cancels a review when you provide its full job ID. Planning, rescue, writing,
apply and resume are available only in Claude Code.

| Command | What it does |
| --- | --- |
| `/agy:review` | Reviews uncommitted changes, the branch against its base (`--base <ref>`), or another ref without checking it out (`--base <ref> --head <ref>`). Takes instructions: text after the flags, or `--instructions-file <path>`. Read-only. |
| `/agy:adversarial-review` | Review that challenges the design and assumptions. Takes focus text. Read-only. |
| `/agy:plan <request>` | Writes an implementation plan. Takes `--request-file <path>`. Read-only. |
| `/agy:rescue <request>` | Hands a debugging or implementation task to agy through the `agy-rescue` subagent (`--request-file <path>` supported). agy edits its own working copy unless `--read-only` is set. The subagent is also how Claude starts a review by itself. |
| `/agy:agy-writing <request>` | Drafts or rewrites prose such as pull request descriptions, comments and release notes. |
| `/agy:status [job]` | Lists jobs, or shows one job with a table of its rounds: duration, time waiting for Claude, tokens, stalls and queued commands. |
| `/agy:result [job]` | Shows a finished job's output. |
| `/agy:resume <job> [file]` | Sends the output of commands agy queued back to agy, and lets it continue. |
| `/agy:apply [job]` | Applies a finished task's changes to your checkout. Use `--check` to test first. |
| `/agy:cancel [job]` | Stops a running job. |
| `/agy:setup` | Checks that agy is installed and signed in. |

Most commands accept `--model`, `--background` and `--isolation
container|host`. `--model` takes a thinking level (`low`, `medium`, `high`,
`xhigh`), a family (`neon`), both (`neon-high`), or a full ID from `agy
models`. Without it, the Gemini model follows the calling Claude model:
Haiku and Sonnet get `gemini-4-hydrogen-high`, Opus and Fable
`gemini-4-neon-high`. The commands pass the
caller's model ID as `--caller-model`. If there is no caller model, a new
job uses the model chosen in agy's settings (the container sign-in's
settings for a container job), and with none set agy picks. A resumed job
keeps its model. A Gemini 4 family on its own gets Google's default level
for it; any other name gets Medium.
A level on its own keeps the family of that model, or uses Neon. Gemini 3.x and other models work by full ID. The
[main README](../../README.md#which-gemini-model-to-pick) explains the
mapping. The default isolation is the container when Docker, the image and a
sign-in a container can copy are all present, otherwise the host; `/agy:setup` says
which, and `/agy:status` shows it per job.

## How a job is contained

agy has no enforced read-only mode: `--mode plan` only adds an instruction to
the prompt. The plugin does not rely on it. Every job starts from a separate
working copy in the plugin's state directory, not in your repository, holding
your `HEAD` plus your uncommitted and untracked changes. The plugin's own git
calls turn off repository hooks, the fsmonitor helper and commit signing, so
setting up the copy runs no program your repository configures, and they pin
the patch format so your diff and colour settings cannot break it.

### Container mode

agy runs in a Linux container (`containers/Dockerfile`) that can see three
things, each mounted at its host path: the working copy, one subdirectory of
the job's state directory (agy's log, the exported patch and the job's own
home) and the plugin's own files (read-only). Nothing else from your machine
is mounted, so there is no `~/.aws`, `~/.ssh` or keychain to read, and the
job record, the baseline snapshot and the result, which the host trusts, are
out of the container's reach. The job's home belongs to that job alone: it
starts empty, receives a copy of the newest agy sign-in at the start of every
round (your own from `~/.gemini/antigravity-cli` or, on macOS, the Keychain,
or the container-only one
from `~/.gemini/agy-container-home`; neither directory is mounted into a
job), with settings that carry only the Google Cloud project and location, and keeps agy's conversation between rounds so a
resume works. A job therefore cannot leave a setting, a hook or a cached
package behind for the next job, whatever repository it was reviewing. The
patch export runs with an empty home, python in isolated mode and no user
git configuration, so nothing a round planted in the job's home runs there.
A `--read-dir` that overlaps either sign-in or the state directory is refused.
Inside the container agy runs with `--dangerously-skip-permissions`: it runs
the commands it needs, including your tests, and edits what it likes, and
all of that lands in the working copy. For a review, a plan or a task round
with `--read-only`, the working copy is mounted read-only, so agy cannot edit
it at all.

- The working copy is a self-contained repository (your `HEAD` tree plus your
  changes as its only commit), not a linked worktree, so nothing inside the
  container reaches your `.git`.
- The patch is computed inside the container too. The host runs no git over
  files agy has written; it only runs `git apply` into your checkout, and
  compares your files against a snapshot of the baseline taken before agy
  started.
- Your repository's own `.agents` hooks and workspace plugins run, inside the
  container, as they would for a developer. A review with `--head <ref>`
  is the exception: its copy is built from that ref's tree with the `.agents`
  directory removed, because the ref is often someone else's pull request and
  its hooks would run beside the job's copy of your sign-in.
- agy's own terminal sandbox is off inside the container (Docker blocks the
  user namespaces it needs); the container is the boundary.
- Jobs run as your user id, and `/agy:setup` refuses an image built for a
  different one, so files the image created and files on the bind mounts
  have one owner without any ownership fix-up at start. Inside the container
  git trusts the working copy and has an identity (`agy plugin`), so agy may
  commit there; only the patch leaves.

The container design follows Matt Pocock's
[Sandcastle](https://github.com/mattpocock/sandcastle) (MIT): a bind-mounted
working copy, the host user's ids baked into the image at build time with a
pre-flight check, git trust and identity set inside the sandbox, caches in the
sandbox home, and container removal on every exit path. No Sandcastle code is
used, so `NOTICE` is unchanged.

What the container does not change: agy still has the network, because it
must reach Google, so a prompt injection in the repository can send out
anything the container can read: the repository, any `--read-dir`
directory, and the job's copy of the sign-in. The repository
already goes to Google for the review, so that part is not new, but it is
not nothing. The job's copy is the same credential as your own `agy`
sign-in, and it is not limited to Gemini: on 10 November 2026, with agy 1.5.4
and a workforce identity federation sign-in, the access token was accepted by
the Resource Manager, Cloud Storage and IAM APIs, so it can do in Google Cloud
whatever your own account can. The file also holds the refresh token; with workforce identity federation it expires with the pool's
session duration (see `NOTES.md`). And a Docker virtual machine on
macOS shares only some directories: the plugin's state directory must be
under your home (Colima's default share), which `/agy:setup` checks.

### Host mode

Without a container, each job gets three layers:

1. **A linked worktree** of your repository as the working copy.
2. **A tool policy.** The plugin installs an agy `PreToolUse` hook in that
   working copy. The copy's `.agents` directory is emptied first and holds
   only that hook, because agy would otherwise run any hooks and workspace
   plugins the repository tracks there on every tool call, outside the policy
   and the sandbox. Your own `.agents` directory is not touched, and edits
   agy makes under `.agents` are not exported.
   - Reads are limited to the working copy and agy's artifact directory. To
     let agy read another directory, pass `--read-dir <path>` (repeatable);
     the prompt tells agy it may read there. A shell command runs without
     Claude only when its program and every option are on a short
     allow-list (`ls`, `cat`, `head`, `tail`, `wc`, `grep`, `find`, `stat`,
     `pwd`, `echo`) and every path it names stays inside those directories.
     A `~` or glob, an option that follows symlinks or reads a list of
     further files, or a file name attached to an option sends the command
     to Claude instead.
   - Reviews and plans may write only agy's own plan artifact. Tasks may write
     only inside the working copy, and never its `.git` or `.agents`
     directory, under any spelling the filesystem maps to them.
   - Web, browser, MCP and subagent tools are blocked in every job.
   - A symlinked `.agents` directory stops the job before it starts, so the
     plugin never removes or writes through a link into your checkout.
3. **A write sandbox on macOS.** agy runs under `sandbox-exec`. It may write
   only to the working copy, `~/.gemini`, the job's state directory and
   temporary directories, and never to your checkout, even when your checkout
   is itself under a temporary directory. Pass `--no-os-sandbox` to turn this
   off. On Linux there is no equivalent layer yet.

In host mode the plugin never passes `--dangerously-skip-permissions`.

## Commands agy wants to run (host mode)

In a container agy runs its own commands. On the host, in print mode agy
refuses any shell command that is not in its own
`permissions.allow` list (in `~/.gemini/antigravity-cli/settings.json`), and
an agy hook cannot override that. The plugin's policy is stricter still. It
lets a command through only when the program is one of `ls`, `cat`, `head`,
`tail`, `wc`, `pwd`, `grep`, `stat`, `echo` or `find` (without `-exec`,
`-delete` or `-fprint`), is named without a path, uses no shell syntax, and
reads only inside the job's allowed directories. git is not on that list,
because repository configuration can make even `git diff` run a program.

Every other command is queued, and the job ends as `needs-approval`. Claude
runs each queued command it judges safe with its own Bash tool, under your
normal Claude Code permission checks, and sends the output back with
`/agy:resume`. agy continues the same conversation in the same working copy.
`/agy:status <job>` shows each round's duration, the time agy waited for
Claude, and the tokens used, so you can see what the approval loop costs.

## Stalls

agy sometimes stalls: its log shows `stream idle timeout: context deadline
exceeded` while it retries the model without output. The timer is agy's
own, 10 minutes since agy 1.5.10 (5 minutes before). The job runner treats
that log line, or about ten and a half minutes without a stream event, as a
stall. While a tool step is running, the stream is silent until
the tool finishes, so the runner then waits up to 30 minutes. On a stall it
stops agy and everything agy started, and resumes the same conversation on
the same model, up to two times. It never moves a job to a lower model: a stall
is a fault in Google's service, and finishing on a weaker model returned weaker
work. `/agy:status` shows each stall.

An expired sign-in is not a stall. When the workforce pool's session ends,
agy logs `"invalid_grant" "Refresh token has expired"` and retries without
end; the runner stops the job at that line and reports a sign-in failure,
instead of waiting for the stall timer and resuming.

## Failure classes and exit codes

A failed job records why as `failure`, shown by `/agy:status` and
`/agy:result`, and a command that waits for the job exits with that class's
code. The idea, and the codes for the classes both plugins share, come from
yuting0624/antigravity-for-claude-code.

| Exit | Class         | Meaning                                                   |
|------|---------------|-----------------------------------------------------------|
| 0    |               | Completed, needs approval, or still running in background |
| 1    | `agy-failed`  | agy failed for a reason no other class names              |
| 2    |               | Usage error: a bad argument, or no git repository         |
| 10   | `quota`       | Google refused the request for quota                      |
| 11   | `signin`      | Not signed in, or the sign-in expired                     |
| 12   | `timeout`     | The job ran past its time limit                           |
| 13   | `agy-missing` | Host agy is missing or its version could not be confirmed as supported |
| 14   | `model`       | agy did not recognise the model                           |
| 15   | `licence`     | Google reclaimed the Gemini Enterprise seat               |
| 16   | `stall`       | Still stalled after two resumes on the same model         |
| 17   | `container`   | Docker is not running, the image is missing or incompatible, or Docker did not remove a job's container |

The class comes from agy's stderr, agy's own log and its error text, never
from the model's reply. The quota class matches Google's `RESOURCE_EXHAUSTED`
status and HTTP 429; neither has been seen from agy here yet.

## Using a Gemini Enterprise licence

See [docs/gemini-enterprise.md](docs/gemini-enterprise.md) for what an
organisation needs before agy works with its Gemini Enterprise licence, and
for the sign-in quirks we hit.

## Testing

`tests/` in the source repository runs the companion against a fake agy that
copies the behaviour recorded from agy 1.4.7, including permission denials,
hook calls, stalls and recorded streams replayed unchanged, and against a
fake docker that runs containers as host processes. CI runs it on Linux and
macOS with Python 3.9 and 3.13; the macOS run exercises the real
`sandbox-exec` profile, and where a Docker daemon is reachable
`test_real_container.py` runs the fake agy inside a real container and checks
that a repository hook, a file outside the working copy and a git clean
filter all stay inside it. The tests use no Gemini licence and no Claude
account. They show the plugin handles agy's recorded behaviour, not that a
live model still behaves that way.

For that, run `scripts/live-smoke.sh --apply` from the source repository on a
machine where agy is signed in (`AGY_SMOKE_ISOLATION=container` or `host`
picks the mode; the default is whatever `/agy:setup` would choose). In a
throwaway repository it runs the
companion's setup, review, adversarial review, plan, task, resume (or, when
agy queued nothing, a `--resume-last` follow-up that must recall a code word
from the first round), result, apply check and cancel against the real agy,
checks each result, and prints the approval-round table. `/agy:rescue` itself is not run: it is a Claude
subagent that calls the same `task` command. Code agy wrote runs only under a
write sandbox, so on Linux those checks are skipped. It uses your Gemini
licence, so without `--apply` it only lists the steps. `--with-claude` also
drives `/agy:setup` and `/agy:review` through `claude --plugin-dir`.

## Gotchas

- **Check what agy quotes.** agy has presented invented text as an exact copy
  of a real file. Claude verifies agy's quotes and `file:line` claims before
  relaying them, and says which it checked.
- **Plans go to agy's artifact directory.** In plan mode agy writes
  `implementation_plan.md` under `~/.gemini/antigravity-cli/brain/<id>/` (for
  a container job, under the job's own home in its state directory); its
  reply only summarises it. `/agy:result` shows the file.
- **Resumes cost tokens.** A resumed conversation carries its history, so
  each approval round costs more input tokens than the last.
- **`--wait` returns after 100 seconds** with the job still running in the
  background, so the Bash call stays inside Claude Code's default timeout.
  The output ends with a `result <job> --wait-seconds 590` command that waits
  for the result. The `agy-rescue` subagent waits up to 590 seconds for
  read-only jobs, with a ten-minute Bash timeout.
- **A container job sees one commit.** Its copy has no history, remotes or
  other refs, and nothing outside the repository. A review therefore gets its
  diff from the host: in the prompt, or, over 400 KiB, as the file
  `.agy-review.diff` in the copy. A task gets only its request text, so the
  companion prints a `Note:` when the request names `origin/...`, `refs/...`,
  `pr/<n>` or a path outside the repository.
- **`Cannot review:` means no review.** When the diff or a file it needs is
  missing, agy is told to return the verdict `cannot-review`, which the
  result shows as `Cannot review: <what is missing>` with no verdict line.
- **`Clearcut responded with HTTP code: 401`** lines in agy's logs are
  telemetry noise.
- **Working copies.** A review's or plan's working copy is removed when the
  job finishes, unless it is waiting on queued commands. A task's working copy
  stays for `/agy:resume` and `/agy:apply`. A finished job idle for seven
  days, in any repository, loses its working copy and its container home;
  its result and patch stay, so `/agy:apply` still works but `/agy:resume`
  does not. The newest 50 finished jobs per repository are kept. To free the
  space at once, run `python3 scripts/agy_companion.py cleanup`. Job state lives under
  `$CLAUDE_PLUGIN_DATA/state`, or the system temporary directory. The
  session-start hook exports that directory to the session as
  `AGY_COMPANION_STATE_DIR`, so commands and hooks share it.
- **Jobs belong to the Claude session that started them.** `/agy:status`,
  and `/agy:cancel`, `/agy:result`, `/agy:apply` and `--resume-last` without
  a job id, see only this session's jobs. Pass a job id to reach another
  session's job; `/agy:status --all` lists them. Run from a plain terminal,
  where no session id is set, the commands see every job.
- **`/agy:apply` checks for drift.** It refuses when a file the task changed
  has changed in your checkout since the job started, in content or mode.
  `--force` skips that check; git still refuses a patch that does not apply.

## Open questions

- **Host mode's future.** Once container mode has run live for a while, the
  host-side policy, sandbox and `/agy:resume` loop are due for removal.
- **The Antigravity Python SDK** offers real permission callbacks, but it
  bills through Gemini Enterprise Agent Platform (formerly Vertex AI) rather
  than a Gemini Enterprise seat.
- **Live tests in CI.** The live smoke test runs by hand only: a live agy
  needs a saved agy sign-in, which is a long-lived secret.

## Licence

Apache License 2.0. Parts are adapted from OpenAI's codex-plugin-cc; see
`NOTICE` for what was changed.
