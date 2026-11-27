"""Run agy inside a Linux container, with the container as the isolation boundary.

The container sees the job's working copy, one subdirectory of the job's
state directory (for agy's log, the exported patch and the job's own home)
and the plugin's own files (read-only). Each is mounted at its host path, so
paths in agy's output, in its log and in the plan artifact mean the same
thing on both sides and nothing has to be translated. Nothing else from the
host is mounted, so a prompt-injected "read ~/.aws/credentials" finds no
such file. The job record, the baseline snapshot and the result stay outside
the mount: the host trusts them, so nothing inside the container may write
them.

The job's home is seeded at the start of every round with a copy of the
newest agy sign-in: the host's own, from its token file or the macOS
Keychain, or the one containers/signin.sh wrote. The home is never shared:
one job cannot leave a setting, a hook or a cached package behind for the
next, whatever repository it was reviewing.

Inside the container agy runs with --dangerously-skip-permissions: it may
run any command and edit any file it can reach, and what it can reach is the
working copy. agy still runs PreToolUse hooks there. The job's home holds
one, mounted read-only with the job's policy file, which refuses agy's
built-in web, browser, MCP and subagent tools unless the job allows web
search. It does not stop a command such as curl from reaching the network.
The patch is computed inside the container as well, so the host never runs
git over files agy has written.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import pwd
import re
import secrets
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import agybin
from .state import INHERITED_STATE_DIR_ENV, PLUGIN_DATA_ENV, codex_state_root, state_root

IMAGE_ENV = "AGY_IMAGE"
DEFAULT_IMAGE = "agy-sandbox:experiment"
HOME_ENV = "AGY_CONTAINER_HOME"
WORKSPACE_PROBE = ".agy-mount-probe-"  # plus a random suffix, see state_mountable
JOB_MOUNT = "container"  # the one job subdirectory the container may write
DOCKER_TIMEOUT = 60
# Git configuration for every command in the container, agy's included. The
# working copy was created by the host user; through a virtual machine's
# shared filesystem its owner can differ from the container's user, and git
# then refuses the repository as "dubious ownership". Nothing outside the
# working copy is mounted, so trusting every directory costs nothing. An
# identity lets agy commit inside the copy if it wants to (only the patch
# leaves the container), and signing is off because there is no key.
_GIT_CONFIG = (
    ("safe.directory", "*"),
    ("user.name", "agy plugin"),
    ("user.email", "agy-plugin@localhost"),
    ("commit.gpgsign", "false"),
)
GIT_ENV = (
    f"--env=GIT_CONFIG_COUNT={len(_GIT_CONFIG)}",
    *[
        flag
        for i, (key, value) in enumerate(_GIT_CONFIG)
        for flag in (f"--env=GIT_CONFIG_KEY_{i}={key}", f"--env=GIT_CONFIG_VALUE_{i}={value}")
    ],
)


def docker_bin() -> Path | None:
    found = shutil.which("docker")
    return Path(found) if found else None


def image_name() -> str:
    return os.environ.get(IMAGE_ENV) or DEFAULT_IMAGE


TOKEN_FILE = "antigravity-oauth-token"  # noqa: S105 -- a file name, not a secret
KEYCHAIN_TOOL = "/usr/bin/security"
KEYCHAIN_SERVICE = "gemini"
KEYCHAIN_ACCOUNT = "antigravity"
KEYRING_PREFIX = "go-keyring-base64:"
DEFAULT_SETTINGS = {"enableTerminalSandbox": False}
HOOKS_FILE = "hooks.json"  # agy reads PreToolUse hooks from this file in its home
JOB_HOOKS = "container-hooks.json"  # the trusted copy, in the job directory, mounted over the home's
POLICY_FILE = "policy.json"


def signin_home() -> Path:
    """Where containers/signin.sh leaves a container-only sign-in and settings.

    One per user, beside the host's own ~/.gemini rather than inside a state
    directory, so every state directory (per plugin install, or per test)
    shares the one sign-in. It is never mounted into a job; each job gets a
    copy.
    """
    explicit = os.environ.get(HOME_ENV)
    if explicit:
        return Path(explicit).resolve()
    return Path.home() / ".gemini" / "agy-container-home"


def _readable_token(token: Path) -> tuple[float, bytes] | None:
    """The token file's modification time and contents, or None when it is missing, empty or not JSON.

    The contents returned are the bytes that were parsed, so a job copies
    exactly what was checked. A file agy is rewriting at this moment fails
    to parse and is skipped for this round rather than copied half-written.
    """
    try:
        mtime = token.stat().st_mtime
        data = token.read_bytes()
    except OSError:
        return None
    checked = _signin_json(data)
    return (mtime, checked) if checked else None


def _signin_json(data: bytes) -> bytes | None:
    """`data` when it is a JSON object holding a token, the shape of agy's token file; otherwise None."""
    try:
        parsed = json.loads(data)
    except (ValueError, RecursionError):
        return None
    return data if isinstance(parsed, dict) and parsed.get("token") else None


def _keychain_signin() -> tuple[float, bytes] | None:
    """The host agy's sign-in from the macOS Keychain as token-file bytes, with its modification time.

    Unless WSL_INTEROP is set, agy on macOS keeps its sign-in in the login
    Keychain (service "gemini", account "antigravity") and leaves a null
    token in its token file. The Keychain value is the JSON the file would
    hold, stored base64-encoded behind go-keyring's prefix, so a job gets
    what a file sign-in would have given it. Reading it raises no prompt,
    because go-keyring writes the item through /usr/bin/security.

    The Keychain belongs to the user rather than to $HOME, so it is read only
    when $HOME is the user's own home directory. A test, or anything else
    that points HOME elsewhere, gets no sign-in from it.

    Any failure means no sign-in from the Keychain, never an error, so a
    usable token file or container sign-in still serves the job.
    """
    if sys.platform != "darwin" or Path.home() != _own_home():
        return None
    query = [KEYCHAIN_TOOL, "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT]
    try:
        attributes = _ask_keychain(query)
        value = _ask_keychain([*query, "-w"])
    except (OSError, subprocess.TimeoutExpired):
        return None
    if attributes.returncode != 0 or value.returncode != 0:
        return None
    checked = _signin_json(_keyring_value(value.stdout.strip()))
    return (_keychain_mtime(attributes.stdout), checked) if checked else None


def _ask_keychain(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, errors="replace", timeout=10, check=False)


def _own_home() -> Path | None:
    """The user's home directory from the user database, or None when it has no entry."""
    try:
        return Path(pwd.getpwuid(os.getuid()).pw_dir)
    except KeyError:
        return None


def _keyring_value(stored: str) -> bytes:
    """The bytes go-keyring stored: base64 behind its prefix, or the plain text without one."""
    if not stored.startswith(KEYRING_PREFIX):
        return stored.encode()
    try:
        return base64.b64decode(stored[len(KEYRING_PREFIX) :].encode("ascii"), validate=True)
    except (binascii.Error, UnicodeEncodeError):
        return b""


def _keychain_mtime(attributes: str) -> float:
    """The item's modification time from `security find-generic-password` output, or 0 when absent.

    The line reads `"mdat"<timedate>=0x... "20261124033118Z\\000"`, a UTC time.
    """
    match = re.search(r'"mdat"<timedate>=\S+\s+"(\d{14})Z', attributes)
    if not match:
        return 0.0
    try:
        stamp = datetime.strptime(match.group(1), "%Y%m%d%H%M%S")
    except ValueError:
        return 0.0
    return stamp.replace(tzinfo=timezone.utc).timestamp()


def _newest_signin() -> tuple[str, Path, bytes] | None:
    """The label, directory and checked token bytes of the newest readable sign-in.

    The host's sign-in is in its token file or in the Keychain, depending on
    whether agy ran with WSL_INTEROP set. Both are labelled "host" and take
    their settings from the host's agy home.
    """
    host = agybin.agy_home()
    signin = signin_home() / ".gemini" / "antigravity-cli"
    candidates = (
        ("host", host, _readable_token(host / TOKEN_FILE)),
        ("host", host, _keychain_signin()),
        ("container", signin, _readable_token(signin / TOKEN_FILE)),
    )
    best: tuple[float, str, Path, bytes] | None = None
    for label, directory, readable in candidates:
        if readable is not None and (best is None or readable[0] > best[0]):
            best = (readable[0], label, directory, readable[1])
    return best[1:] if best else None


def signin_source() -> tuple[str, Path] | None:
    """Which sign-in a job copies: ("host", dir) or ("container", dir), whichever token is newer.

    The host's own `agy` sign-in works inside a container unchanged (a fresh
    container listed the Gemini 4 Argon models with a copy, 7 November
    2026), so a morning sign-in on the host covers container jobs too. It is
    read from ~/.gemini/antigravity-cli or, on macOS, from the Keychain. The
    container sign-in from containers/signin.sh is used when it is newer,
    or when the host has no sign-in a container can use, as on a Linux host
    whose agy keeps its token in the Secret Service keyring.
    """
    found = _newest_signin()
    return (found[0], found[1]) if found else None


def _job_settings(label: str, source: Path) -> bytes:
    """settings.json for a job's home.

    The container sign-in's settings were written for containers by
    containers/signin.sh and are copied as they are. From the host's settings
    only the Google Cloud project and location are taken: its permission
    rules, hooks and servers belong to the host, and agy's own terminal
    sandbox cannot start under Docker's default seccomp profile.
    """
    path = source / "settings.json"
    if label == "container" and path.is_file():
        return path.read_bytes()
    settings = dict(DEFAULT_SETTINGS)
    try:
        host = json.loads(path.read_bytes())
    except (OSError, ValueError):
        host = {}
    if isinstance(host, dict) and "gcp" in host:
        settings["gcp"] = host["gcp"]
    return (json.dumps(settings) + "\n").encode()


def job_home(job_dir: Path) -> Path:
    """The job's own HOME inside its mounted subdirectory."""
    return job_mount(job_dir) / "home"


def hook_settings(plugin_root: Path, job_dir: Path) -> bytes:
    """The hooks.json that runs the plugin's policy hook on every tool call in the container.

    The hook reads the job's policy file, which is mounted read-only at its
    host path, and logs into the job's mounted subdirectory.
    """
    words = ["python3", "-I", str(plugin_root / "scripts" / "agy_hook.py"), str(job_dir), str(job_mount(job_dir))]
    command = " ".join(shlex.quote(word) for word in words)
    hooks = {
        "agy-plugin-policy": {
            "PreToolUse": [{"matcher": ".*", "hooks": [{"type": "command", "command": command, "timeout": 30}]}]
        }
    }
    return (json.dumps(hooks, indent=2) + "\n").encode()


def seed_home(job_dir: Path, hooks: bytes | None = None) -> Path:
    """Give the job its own home with a copy of the newer sign-in and settings, and return it.

    The home belongs to one job: a new job starts empty, so nothing a job on
    another repository did can reach it. Within a job the home persists
    between rounds, because agy keeps the conversation it resumes with
    (conversations/<id>.db) and its plan artifacts there; only the sign-in
    and settings are written afresh each round, so a new sign-in or a
    setting agy changed does not carry over. The refresh token in the copy
    keeps working: refreshing does not rotate it, so copies refreshed
    independently stay valid until the pool session ends.

    With hooks, the job directory gets the trusted copy that the container
    mounts read-only over the home's hooks.json, and the home gets the same
    bytes as the mount point.
    """
    if hooks is not None:
        (job_dir / JOB_HOOKS).write_bytes(hooks)
    found = _newest_signin()
    # Every directory below the job directory is agent-writable, so a round
    # could have replaced one with a link to somewhere on the host. Descend
    # with directory descriptors that refuse links, and write through them.
    fd = os.open(str(job_dir), os.O_RDONLY | os.O_DIRECTORY)
    try:
        for name in (JOB_MOUNT, "home", ".gemini", "antigravity-cli"):
            fd = _descend(fd, name)
        if found is None:
            _write_private(fd, "settings.json", (json.dumps(DEFAULT_SETTINGS) + "\n").encode())
        else:
            label, source, token = found
            _write_private(fd, TOKEN_FILE, token)
            _write_private(fd, "settings.json", _job_settings(label, source))
        if hooks is not None:
            _write_private(fd, HOOKS_FILE, hooks)
    finally:
        os.close(fd)
    return job_home(job_dir)


def _descend(parent_fd: int, name: str) -> int:
    """Open (creating if needed) the directory `name` under parent_fd, refusing a link.

    On success the parent descriptor is closed and the child returned; on
    failure the parent stays open for the caller to close.
    """
    try:
        os.mkdir(name, 0o700, dir_fd=parent_fd)
    except FileExistsError:
        pass
    try:
        child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
    except OSError as exc:
        raise RuntimeError(f"the job's home component {name!r} is not a plain directory: {exc.strerror}") from exc
    os.close(parent_fd)
    return child


def _write_private(dir_fd: int, name: str, data: bytes) -> None:
    """Create or replace a file under dir_fd with mode 0600 from the start, never through a link."""
    try:
        if stat.S_ISLNK(os.lstat(name, dir_fd=dir_fd).st_mode):
            os.unlink(name, dir_fd=dir_fd)
    except FileNotFoundError:
        pass
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600, dir_fd=dir_fd)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
    os.chmod(name, 0o600, dir_fd=dir_fd, follow_symlinks=False)


def export_home(job_dir: Path) -> Path:
    """An empty home for the export container, created afresh each time.

    The export runs python and git with the working copy writable, so it
    must not pick up start-up files a round planted in the job's home
    (a sitecustomize module, a user git configuration).
    """
    home = job_mount(job_dir) / "export-home"
    if home.is_symlink() or home.is_file():
        home.unlink()
    elif home.is_dir():
        remove_tree(home)
    home.mkdir(parents=True)
    return home


def remove_tree(path: Path) -> None:
    """rmtree that first makes directories a round left read-only writable again."""

    def retry(func, target, _exc):
        parent = os.path.dirname(target)
        for candidate in (target, parent):
            try:
                os.chmod(candidate, 0o700)
            except OSError:
                pass
        func(target)

    shutil.rmtree(path, onerror=retry)


def overlaps_protected(directory: Path) -> str | None:
    """Why a --read-dir may not be mounted: it would expose the sign-in or other jobs' state."""
    wanted = Path(os.path.realpath(directory))
    protected_dirs = [
        ("the container sign-in", signin_home()),
        ("the host's agy sign-in", agybin.agy_home()),
        ("the plugin's state directory", state_root()),
        # Both clients' jobs keep token copies in their homes. A Codex run points
        # state_root() at its own directory, so Claude Code's default locations
        # are listed here too.
        ("the Codex job state directory", codex_state_root()),
        ("Claude Code's plugin data", Path.home() / ".claude" / "plugins" / "data"),
        ("the fallback job state directory", Path(tempfile.gettempdir()) / "agy-companion"),
    ]
    if inherited := os.environ.get(INHERITED_STATE_DIR_ENV):
        protected_dirs.append(("the inherited job state directory", Path(inherited)))
    if plugin_data := os.environ.get(PLUGIN_DATA_ENV):
        protected_dirs.append(("Claude Code's plugin data", Path(plugin_data)))
    if config_dir := os.environ.get("CLAUDE_CONFIG_DIR"):
        protected_dirs.append(("Claude Code's configured plugin data", Path(config_dir) / "plugins" / "data"))
    for label, protected in protected_dirs:
        real = Path(os.path.realpath(protected))
        if wanted == real or wanted in real.parents or real in wanted.parents:
            return f"{directory} overlaps {label} ({protected})"
    return None


def _docker(*args: str, timeout: float = DOCKER_TIMEOUT) -> subprocess.CompletedProcess | None:
    binary = docker_bin()
    if not binary:
        return None
    try:
        return subprocess.run([str(binary), *args], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None


def daemon_reachable() -> bool:
    proc = _docker("info", "--format", "{{.ServerVersion}}", timeout=15)
    return bool(proc and proc.returncode == 0)


def image_present() -> bool:
    proc = _docker("image", "inspect", "--format", "{{.Id}}", image_name(), timeout=15)
    return bool(proc and proc.returncode == 0)


def image_version() -> tuple[int, ...] | None:
    proc = _docker(
        "run",
        "--rm",
        "--pull=never",
        "--network=none",
        f"--user={os.getuid()}:{os.getgid()}",
        image_name(),
        "--version",
        timeout=30,
    )
    return agybin.parse_version(proc.stdout + proc.stderr) if proc and proc.returncode == 0 else None


def image_user() -> str | None:
    """The image's USER as docker reports it ("501:20", "agent" or ""), or None when the inspection failed."""
    proc = _docker("image", "inspect", "--format", "{{.Config.User}}", image_name(), timeout=15)
    if not proc or proc.returncode != 0:
        return None
    return proc.stdout.strip()


def image_user_matches(user: str | None) -> bool:
    """Whether the image was built for this user.

    Jobs run as the host user so bind-mounted files have one owner, and the
    image's own files (agy's home skeleton, uv) must belong to that same
    user. A numeric USER is compared; a name or no USER cannot be checked
    and passes; a failed inspection does not.
    """
    if user is None:
        return False
    uid, _, gid = user.partition(":")
    if not (uid.isdigit() and gid.isdigit()):
        return True
    return user == f"{os.getuid()}:{os.getgid()}"


def job_mount(job_dir: Path) -> Path:
    return job_dir / JOB_MOUNT


def state_mountable() -> bool:
    """Whether the container can write into the plugin's state directory.

    A Docker virtual machine on macOS shares only some host directories
    (Colima shares the home directory, not /var/folders), so a state root in
    the system temporary directory cannot hold a job. The probe touches a
    file in the state root from inside the container. The name is fresh
    each time: the virtual machine's shared filesystem caches a file the host
    has since deleted, and touching that name again creates nothing the host
    can see.
    """
    probes = []
    for directory in (state_root(),):
        directory.mkdir(parents=True, exist_ok=True)
        probes.append(directory / (WORKSPACE_PROBE + secrets.token_hex(4)))
    proc = _docker(
        "run",
        "--rm",
        f"--user={os.getuid()}:{os.getgid()}",
        "--entrypoint",
        "touch",
        *[f"--volume={p.parent}:{p.parent}" for p in probes],
        image_name(),
        *[str(p) for p in probes],
        timeout=DOCKER_TIMEOUT,
    )
    written = all(p.is_file() for p in probes)
    for probe in probes:
        probe.unlink(missing_ok=True)
    return bool(proc and proc.returncode == 0 and written)


def report() -> dict[str, object]:
    binary = docker_bin()
    reachable = bool(binary) and daemon_reachable()
    present = reachable and image_present()
    user = image_user() if present else None
    version = image_version() if present else None
    source = signin_source()
    return {
        "image_agy_version": ".".join(map(str, version)) if version else None,
        "image_version_problem": agybin.version_problem(version) if present else None,
        "docker": str(binary) if binary else None,
        "docker_reachable": reachable,
        "image": image_name(),
        "image_present": present,
        "image_user": user,
        "image_user_matches": present and image_user_matches(user),
        "state_mountable": present and state_mountable(),
        "signin_home": str(signin_home()),
        "container_signed_in": source is not None,
        "signin_source": (source or ("none",))[0],
    }


def unavailable_reason(status: dict[str, object] | None = None) -> str | None:
    """Why a container job cannot start, or None when one can."""
    status = status if status is not None else report()
    if not status["docker"]:
        return "docker is not installed or not on PATH"
    if not status["docker_reachable"]:
        if shutil.which("colima"):
            return "the Docker daemon is not reachable; Colima is probably stopped: run `colima start`"
        return "the Docker daemon is not reachable (is Docker Desktop running?)"
    if not status["image_present"]:
        return (
            f"the image {status['image']} is not built (or was deleted); run containers/build.sh "
            "from the plugin's source repository. The container sign-in is kept, so no new sign-in is needed"
        )
    if not status["image_user_matches"]:
        if status["image_user"] is None:
            return f"docker could not inspect the image {status['image']}"
        return (
            f"the image {status['image']} was built for user {status['image_user']}, not "
            f"{os.getuid()}:{os.getgid()}; rebuild it with containers/build.sh"
        )
    if status["image_version_problem"]:
        return f"image {status['image']}: {status['image_version_problem']}; rebuild it with containers/build.sh"
    if not status["state_mountable"]:
        return (
            f"the container cannot write to the state directory {state_root()}; "
            "set AGY_COMPANION_STATE_DIR to a directory under your home"
        )
    if not status["container_signed_in"]:
        return (
            "agy is not signed in: run `agy` in a terminal and sign in (containers use a copy of that "
            f"sign-in), or run containers/signin.sh for a container-only sign-in in {signin_home()}"
        )
    return None


def container_name(job_id: str, generation: int) -> str:
    return f"agy-{job_id}-{generation}"


class Mounts:
    """What a job's containers may see, shared by the agy turn and the export
    so that a program agy relies on (a git filter in the home, say) is there
    for both."""

    def __init__(self, worktree: Path, job_dir: Path, plugin_root: Path, read_dirs: list[Path], writable: bool):
        self.worktree = worktree
        self.job_dir = job_dir
        self.job_mount = job_mount(job_dir)
        self.plugin_root = plugin_root
        self.read_dirs = read_dirs
        self.writable = writable
        self.home = job_home(job_dir)

    def prefix(self, name: str) -> list[str]:
        """docker run options: identity, environment and the volumes."""
        binary = docker_bin()
        if not binary:
            raise RuntimeError("docker is not installed or not on PATH")
        if not self.home.is_dir():
            raise RuntimeError(f"the job's home {self.home} has not been seeded")
        policy = self.job_dir / POLICY_FILE
        hooks = self.job_dir / JOB_HOOKS
        # Docker would create a missing source as a directory on the host.
        for path in (policy, hooks):
            if not path.is_file():
                raise RuntimeError(f"the job's {path.name} has not been written")
        home_hooks = self.home / ".gemini" / "antigravity-cli" / HOOKS_FILE
        command = [
            str(binary),
            "run",
            "--rm",
            "--init",
            f"--name={name}",
            f"--user={os.getuid()}:{os.getgid()}",
            f"--env=HOME={self.home}",
            "--env=PYTHONDONTWRITEBYTECODE=1",
            *GIT_ENV,
            f"--volume={self.worktree}:{self.worktree}" + ("" if self.writable else ":ro"),
            f"--volume={self.job_mount}:{self.job_mount}",
            f"--volume={self.plugin_root}:{self.plugin_root}:ro",
            # Read-only, so agy cannot change its own policy or remove the hook.
            f"--volume={policy}:{policy}:ro",
            f"--volume={hooks}:{home_hooks}:ro",
        ]
        for directory in self.read_dirs:
            command.append(f"--volume={directory}:{directory}:ro")
        command.append(f"--workdir={self.worktree}")
        return command


def wrap(agy_args: list[str], *, name: str, mounts: Mounts) -> list[str]:
    """The docker command that runs agy's arguments inside the container.

    agy_args comes from agybin.build_command without the binary, which the
    image's entrypoint supplies. --init gives the container a real init, so
    the SIGTERM that docker forwards on stop reaches agy and its children.
    A read-only round (a review, a plan, or a task round with --read-only)
    mounts the working copy read-only, which is what enforces it: inside the
    container agy has no permission checks.
    """
    command = mounts.prefix(name)
    command.insert(2, "--interactive")
    return command + [image_name(), *agy_args[1:], *agybin.SKIP_PERMISSIONS_FLAGS]


def export_command(baseline: str, *, name: str, mounts: Mounts, home: Path) -> list[str]:
    """A docker command that computes the job's patch inside the container,
    into the job's mounted subdirectory.

    It runs with an empty home, python in isolated mode and no user or
    system git configuration, so nothing a round left behind runs here.
    """
    companion = mounts.plugin_root / "scripts" / "agy_companion.py"
    prefix = [f"--env=HOME={home}" if o.startswith("--env=HOME=") else o for o in mounts.prefix(name)]
    return [
        *prefix,
        "--env=GIT_CONFIG_GLOBAL=/dev/null",
        "--env=GIT_CONFIG_NOSYSTEM=1",
        "--entrypoint",
        "python3",
        image_name(),
        "-I",
        str(companion),
        "_export",
        str(mounts.worktree),
        baseline,
        str(mounts.job_mount),
    ]


def remove(name: str, timeout: float = 30) -> bool:
    """Stop and remove the container, if it still exists, and say whether it is gone.

    Stopping the docker client does not stop the container, so this runs
    after every turn. When the removal itself fails or times out, Docker is
    asked whether the container still exists; False means it does, or that
    Docker could not say.
    """
    removed = _docker("rm", "--force", name, timeout=timeout)
    if removed is not None and removed.returncode == 0:
        return True
    listed = _docker("ps", "--all", "--quiet", "--filter", f"name=^{name}$", timeout=timeout)
    return bool(listed and listed.returncode == 0 and not listed.stdout.strip())
