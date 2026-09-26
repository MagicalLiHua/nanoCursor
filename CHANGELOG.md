# Changelog

## Unreleased

- Added opt-in model-assisted Bash approval for the main interactive agent, with manual fallback and explicit permission rules preserved.
- Preserved parent permission rules in Team workers and rejected malformed permission files at startup and execution.
- Stopped noninteractive tasks when approval is required; added nonzero terminal failure statuses and tool-ID-based result summaries.
- Restored completed-turn file and conversation checkpoints, detected external edits and damaged backups, and persisted rewound conversations across session resume.
- Kept persisted tool outputs available after compaction instead of deleting files still referenced by conversations.
- Bounded Bash capture and file/search reads, sent output previews to the UI, and distinguished recoverable compaction warnings from fatal errors in CLI and evaluation runs.
- Added an acceptEdits shortcut to ordinary WriteFile/EditFile approval prompts, with immediate status updates and existing command/path/rule checks preserved.
- Added independent tool installation instructions, pinned runtime/build constraints, package resource checks and installation smoke tests.
- Added first-run setup, private user-level credential storage, explicit environment-variable credentials and a saved default provider.
- Added environment-only connections from DeepSeek/Anthropic/OpenAI variable groups, explicit `--env` selection, and setup defaults from available environment values.
- Added `doctor`, `--version`, positional workspace paths and `--cwd`; help and version queries no longer write project files.
- Made project configuration review explicit before startup processes, with credentials bound to their provider endpoint.
- Fixed partial configuration overrides, explicit false/default resets and versioned list merge semantics while preserving legacy configuration.
- Made Worktree restoration a user choice and kept the displayed directory aligned with actual tool execution.
- Unified application paths through optional `NANOCURSOR_HOME`, preserved project history, and moved bounded diagnostic logs into the user data directory.
- Added Linux/macOS installation CI and an opt-in draft release workflow; no automatic public publishing or background updates.

## 3.0.0 - 2026-09-02

- Rebuilt nanoCursor as a focused Python terminal Coding Agent.
- Added external-grader integration and the AgentEval evaluation toolkit.
- Added 72 privacy-safe run records from a controlled nanoCursor/Pi comparison.
- Added reproducible aggregate analysis, SVG charts and Bad Case attribution.
- Fixed Hook enforcement for direct streaming tool execution.
- Standardized the package, CLI and configuration names as `nanocursor`.

The previous application architecture is preserved by the `legacy-v2.0.0` tag.
