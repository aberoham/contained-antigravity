# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/). Future changes go under **Unreleased** and are assigned a version and release date when released.

## Unreleased

Planned for Codex preview `0.2.0`:

### Changed

- **Breaking:** Renamed the bundled plugin install ID from `agy@agy-cc` to `agy@contained-antigravity` (the plugin name remains `agy`). See the [migration guide](README.md#migrating-from-agy-cc) to upgrade an existing installation.

## 0.1.0 (Codex preview)

### Added

- Initial Codex preview supporting read-only reviews and adversarial reviews, with a separate Codex job store and full review IDs for follow-up commands.

### Security

- Container `--read-dir` checks now also refuse directories overlapping the Codex job-state root, including when launched by Claude Code, because per-job homes can contain copies of sign-in material ([PR #36](https://github.com/aberoham/contained-antigravity/pull/36)).

### Fixed

- Missing `agy` errors now point Codex users to `agy_codex.py setup`, and approval stops now report queued commands or state that none were recorded ([PR #36](https://github.com/aberoham/contained-antigravity/pull/36)).
