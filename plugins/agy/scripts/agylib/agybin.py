"""Find the agy binary and build its command line.

Prompts always go to agy on stdin as stream-json, never as an argument, so
nothing depends on shell quoting. `--print=` (empty, attached with `=`) turns
on print mode without letting `-p` swallow the next flag as its prompt.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

AGY_BIN_ENV = "AGY_BIN"
# 1.5.10 waits 10 minutes, not 5, before it treats a silent model as stalled
# and retries the turn, which gives High-level turns room to think.
MIN_VERSION = (1, 5, 10)
# Only a container job gets this flag, and only container.wrap adds it:
# build_command refuses it, so a host-mode command can never carry it.
SKIP_PERMISSIONS_FLAGS = ("--dangerously-skip-permissions",)

GEMINI_GENERATION = "gemini-4"
LEVELS = ("low", "medium", "high", "xhigh")
# Google's default thinking level for each Gemini 4 family, used when
# `--model` names a family without a level. Any other family gets medium.
FAMILY_LEVELS = {"neon": "high", "hydrogen": "high", "argon": "medium", "carbon": "medium"}
DEFAULT_LEVEL = "medium"
# Antigravity's default family for new users, used when a bare level such as
# `--model high` has no model with a level to take its family from.
FALLBACK_FAMILY = "neon"
# The plugin's choice of Gemini model for each Claude model: Hydrogen for
# Haiku and Sonnet, Neon for Opus and Fable, each at Google's default level.
# The README's "Which Gemini model to pick" section gives the reasoning.
CALLER_MODELS = {
    "haiku": "gemini-4-hydrogen-high",
    "sonnet": "gemini-4-hydrogen-high",
    "opus": "gemini-4-neon-high",
    "fable": "gemini-4-neon-high",
}
# agy's settings.json stores the model by display name, such as "Gemini 4 Neon (Medium)".
SETTINGS_MODEL = re.compile(r"^Gemini (\d[\d.]*) ([A-Za-z ]+?) \((Low|Medium|High|XHigh)\)$")
_LEVEL_GROUP = "|".join(LEVELS)
# A model ID that ends in a thinking level, such as gemini-3.1-pro-low.
LEVELLED_MODEL = re.compile(rf"^(gemini-[\w.-]+)-(?:{_LEVEL_GROUP})$")
# A family with an optional level, such as neon or neon-high.
SHORTHAND = re.compile(rf"^([a-z]+)(?:-({_LEVEL_GROUP}))?$")
CLAUDE_MODEL = re.compile(r"^claude-(haiku|sonnet|opus|fable)-")

STALL_LOG_PATTERN = re.compile(r"stream idle timeout")


def agy_home() -> Path:
    return Path.home() / ".gemini" / "antigravity-cli"


def candidate_paths() -> list[Path]:
    paths = []
    explicit = os.environ.get(AGY_BIN_ENV)
    if explicit:
        paths.append(Path(explicit))
    found = shutil.which("agy")
    if found:
        paths.append(Path(found))
    home = Path.home()
    paths += [
        Path("/usr/local/bin/agy"),
        home / ".local" / "bin" / "agy",
        home / ".local" / "share" / "agy" / "agy",
    ]
    return paths


def find_agy() -> Path | None:
    """Return the first executable agy. Shell functions named agy are invisible here by design."""
    for path in candidate_paths():
        if path.is_file() and os.access(path, os.X_OK):
            return path
    return None


def parse_version(text: str) -> tuple[int, ...] | None:
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", text)
    return tuple(int(part) for part in match.groups()) if match else None


def agy_version(binary: Path) -> tuple[int, ...] | None:
    try:
        out = subprocess.run([str(binary), "--version"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return parse_version(out.stdout + out.stderr)


def settings_model(settings: dict[str, Any]) -> str | None:
    """The model ID for the display name in agy's settings, or None when there is none or it does not parse."""
    match = SETTINGS_MODEL.match(str(settings.get("model") or ""))
    if not match:
        return None
    version, family, level = match.groups()
    return f"gemini-{version}-{family.lower().replace(' ', '-')}-{level.lower()}"


def caller_model(caller: str | None) -> str | None:
    """The Gemini model mapped to a Claude model ID such as claude-opus-5-5, or None for any other caller."""
    match = CLAUDE_MODEL.match((caller or "").strip().lower())
    return CALLER_MODELS[match.group(1)] if match else None


def resolve_model(
    name: str | None,
    *,
    caller: str | None = None,
    settings: dict[str, Any] | None = None,
    current: str | None = None,
) -> str | None:
    """Turn a --model value into an agy model ID, or None to let agy choose.

    Without a name, the calling Claude model decides through CALLER_MODELS,
    then the model in agy's settings; None means agy picks its own default.
    A bare level (`high`) swaps the level of `current` (a resumed job's
    model) when there is one, otherwise of that default, keeping its family
    and generation; it falls back to Neon when that model has no level. A family (`hydrogen`) or a
    family and level (`neon-high`) becomes a Gemini 4 ID, which agy rejects
    if the family does not exist. Anything else, including a full `gemini-`
    ID, passes through unchanged.
    """
    base = caller_model(caller) or settings_model(settings or {})
    if not name:
        return base
    lowered = name.lower()  # agy shows levels capitalised: High, XHigh
    if lowered in LEVELS:
        levelled = LEVELLED_MODEL.match(current or base or "")
        prefix = levelled.group(1) if levelled else f"{GEMINI_GENERATION}-{FALLBACK_FAMILY}"
        return f"{prefix}-{lowered}"
    shorthand = None if lowered.startswith("gemini-") else SHORTHAND.match(lowered)
    if shorthand:
        family, level = shorthand.groups()
        return f"{GEMINI_GENERATION}-{family}-{level or FAMILY_LEVELS.get(family, DEFAULT_LEVEL)}"
    return name


def build_command(
    binary: Path,
    *,
    model: str | None,
    mode: str | None,
    log_file: Path,
    conversation_id: str | None = None,
    json_schema: Path | None = None,
) -> list[str]:
    args = [
        str(binary),
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        *(["--model", model] if model else []),
        "--log-file",
        str(log_file),
        "--print-timeout",
        "0s",
    ]
    if mode:
        args += ["--mode", mode]
    if conversation_id:
        args += ["--conversation", conversation_id]
    if json_schema:
        args += ["--json-schema", str(json_schema)]
    args.append("--print=")
    for flag in SKIP_PERMISSIONS_FLAGS:
        if flag in args:
            raise ValueError(f"refusing to pass {flag} to agy")
    return args


def user_message(text: str) -> str:
    """One stream-json input line carrying a user turn."""
    return json.dumps({"event": "user", "message": {"role": "user", "content": text}}) + "\n"


def read_settings(directory: Path | None = None) -> dict[str, Any]:
    """agy's settings.json from `directory`, by default the host's agy home; {} when unreadable."""
    try:
        with open((directory or agy_home()) / "settings.json", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


# One row of `agy models`: the model id, a tab, then its display name.
MODEL_ROW = re.compile(r"^(?:gemini|claude)-[\w.-]+\t\S")


def token_present() -> bool:
    token = agy_home() / "antigravity-oauth-token"
    return token.is_file() and token.stat().st_size > 0


def sign_in_check(binary: Path) -> tuple[bool | None, str]:
    """Ask agy itself whether it is signed in, by listing models.

    agy may keep its sign-in in the token file or in the operating system's
    keychain, so the file alone does not decide it. Listing models sends no
    prompt and needs a sign-in the server accepts. agy exits 0 even when it
    refuses, so the output decides: model rows with no other output and a
    zero exit mean signed in, a request to sign in means not, and anything
    else (a timeout, a licence error) is unknown, with agy's message as the
    detail.
    """
    try:
        proc = subprocess.run(
            [str(binary), "models"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return None, "`agy models` did not answer within 30 seconds"
    except OSError as exc:
        return None, f"could not run `agy models`: {exc}"
    return judge_models_listing(proc.returncode, proc.stdout + proc.stderr)


def judge_models_listing(returncode: int, output: str) -> tuple[bool | None, str]:
    """Decide from `agy models` output whether agy is signed in, as sign_in_check describes.

    containers/signin.sh calls this too, so both checks apply one rule.
    """
    lines = [line.strip() for line in output.splitlines()]
    lines = [line for line in lines if line and line != "Fetching available models..."]
    rows = [line for line in lines if MODEL_ROW.match(line)]
    other = [line for line in lines if not MODEL_ROW.match(line)]
    if any("please sign in" in line.lower() for line in other):
        return False, next(line for line in other if "please sign in" in line.lower())
    if rows and not other and returncode == 0:
        return True, "agy listed its models"
    if other:
        return None, other[0][:200]
    return None, f"`agy models` printed no models (exit {returncode})"
