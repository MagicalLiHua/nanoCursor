# Changelog

## Unreleased

- Added durable execution intents, observed results, generation-aware session projection and project recovery gates. Incomplete model streams no longer start tool batches; unknown outcomes require an explicit user acknowledgement and are never silently replayed.
- Added `/recover` and the model-free `nanocursor recover` entrypoint, with structured recovery reports and noninteractive exit codes 3 (`recovery_required`) and 4 (`recovery_storage_error`). Connected Hooks, background/Skill/Team lifecycles, MCP and maintenance paths to the shared execution record; retained historical result timestamps and stopped the deferred browser execution entrypoint.
- Persisted task-start file checkpoints and before/expected-after versions in the shared recovery store. Added stable-ID restore previews, explicit `apply`, resumable per-file restore progress, idempotent conversation boundaries and durable external-edit barriers. File rollback covers built-in file tools; Bash, MCP, Git and project-external effects remain outside its guarantee.
- Added checkpoint retention/occupancy commands and pinning; preserved file versions, unfinished restores and referenced session evidence. Reject linked/special file targets and block protected writes when backup publication fails.
- Hardened Worktree creation and removal: preserve existing branches, record the original commit and workspace identity, treat Git query failures as unknown, retain failed/ignored/unmerged outcomes, and require a content-bound user decision before removal. Added `--worktree` with explicit committed-baseline acceptance for dirty sources; dependency sharing and ignored-file copying are now opt-in.

- Added the main-interactive `ManageMCP` tool to configure/start, stop and inspect MCP servers, with persistent user settings, single-use approval prompts, conflict checks and disabled-on-failure startup state. Stopped services revoke tools without implicit reconnect; MCP transports now own their connection and cleanup in the same task.
- Added `/tools [all|enabled|disabled]` to list tool sources, enablement and deferred discovery state without a model request; `/mcp` also distinguishes explicitly stopped services.

- Recall relevant memory locally before the first answer by default, with bounded safe reads, optional model selection, shared context budgets, source/version deduplication and durable replacement across resume/compaction. Expose controls and separate selector usage through `/memory recall`.
- Apply Skill configuration consistently across slash commands and LoadSkill: inline inherits the main session; fork resolves configured providers and enforces an isolated subset of tools, discovery and parent permissions. Added bounded session-owned results, cancellation, diagnostics and independent client/Hook cleanup.
- Reject invalid or unsupported Skill declarations instead of silently ignoring them or executing stale cached definitions. Fork defaults now exclude main-session control tools; `allowed-tools` preapproval remains unsupported (use `tools` for capability restriction).

- Set the generic context-window fallback to 200k while preserving explicit settings, provider metadata and model-specific limits.
- Restricted automatic memory writes to validated names and trusted directories, rejected symbolic-link redirects, and limited Plan write exceptions to the allocated plan file.
- Replaced command Hook context interpolation with environment variables or JSON stdin; reject legacy templates and unsupported agent Hooks with configuration diagnostics.
- Made Teams explicitly opt-in, stopped actual worker tasks before closing, and retained worktrees, branches and recovery records. Full Team messaging and persistent follow-up remain experimental and incomplete.
- Guarded session changes during active work, tracked task ownership, and waited for cancellation and persistence before switching or exiting; preserved session-specific background results.
- Repaired append-after-recovery history boundaries, required complete valid summaries before compaction, and handled failed/incomplete Responses streams without reporting false success. Output-limit continuation keeps the configured per-request cap.
- Distinguished saved session records from metadata failures to avoid duplicate results or rolling back committed compaction; corrected the OpenAI SDK minimum to 1.66.3 and kept keyless authentication compatible with supported SDK versions.
- Fixed numbered session resume, manual compaction based on current context size, and Skill forks receiving an independent snapshot of the current conversation.
- Added opt-in background memory consolidation with bounded tool-free proposals, conflict checks, an active-memory index migration and retained source bodies; expose session controls and separate usage through `/memory consolidate`.
- Completed @file selection with cursor-aware insertion, directory navigation, quoted paths and mouse/keyboard support; bounded UTF-8 attachments by bytes and kept internal prompts from implicitly attaching files.
- Fixed missing DeepSeek streaming usage, preserved finish reasons and cache accounting, and distinguished unavailable usage from zero in terminal status.
- Simplified terminal separators and message styling, displayed small positive context usage as <1%, and moved concise completion timing after the answer.
- Added a responsive two-line terminal status bar and live F2 details for model, reasoning, context, permissions, approval activity, MCP, tools and cumulative usage.
- Kept context estimates separate from cumulative usage, anchored final assistant responses to API usage, and corrected MCP tool counting.
- Refined terminal spacing and colors, made tool summaries clickable, and kept errors visible with literal, bounded output previews.
- Added opt-in model-assisted Bash approval for the main interactive agent, with manual fallback and explicit permission rules preserved.
- Preserved parent permission rules in Team workers and rejected malformed permission files at startup and execution.
- Stopped noninteractive tasks when approval is required; added nonzero terminal failure statuses and tool-ID-based result summaries.
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
