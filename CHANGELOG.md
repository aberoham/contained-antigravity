# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/). Future changes go under **Unreleased** and are assigned a version and release date when released.

## Unreleased

## 0.2.0 (Codex preview)

### Changed

- **Breaking:** Renamed the bundled plugin install ID from `agy@agy-cc` to `agy@contained-antigravity` (the plugin name remains `agy`). See the [migration guide](README.md#migrating-from-agy-cc) to upgrade an existing installation.

## 0.1.0 (Codex preview)

### Added

- Initial Codex preview supporting read-only reviews and adversarial reviews, with a separate Codex job store and full review IDs for follow-up commands.

### Security

- Container `--read-dir` checks refuse directories that overlap either client's job state, because per-job homes can contain copies of sign-in material. Claude Code jobs refuse the Codex job-state root. Codex jobs refuse Claude Code's plugin data (the default location, `CLAUDE_PLUGIN_DATA` and `CLAUDE_CONFIG_DIR`), an inherited state directory and the fallback state directory ([PR #36](https://github.com/aberoham/contained-antigravity/pull/36)).

### Fixed

- Missing `agy` errors point Codex users to `agy_codex.py setup` ([PR #36](https://github.com/aberoham/contained-antigravity/pull/36)).
- A Codex review that stops for approval reports the queued commands, or says that none were recorded, and removes its git worktree, because Codex cannot resume it ([PR #36](https://github.com/aberoham/contained-antigravity/pull/36)).
- `agy_codex.py` imports its modules under `python3 -P` and `PYTHONSAFEPATH` ([PR #36](https://github.com/aberoham/contained-antigravity/pull/36)).
