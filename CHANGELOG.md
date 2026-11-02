# Changelog

This file uses the basic organization of [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/).

The source repository is now public. No formal GitHub Release is established by that fact. The version sections below are reconstructed development milestones, not GitHub Releases or verified publication dates. Their design records are dated 2026-11-01; no release dates are invented. An Unreleased entry describes implemented changes awaiting publication, not planned functionality.

## Unreleased

### CI

- Added a least-privilege Windows offline-test workflow with pinned Actions, locked live/llm extras and explicit test counts. The maintainer confirmed the first push-triggered Windows CI run passed 151 tests with no failures, errors or skips on 2026-11-02; this is remote offline acceptance, not live-service integration.

### Docs

- Added repository file attributes for consistent text line endings and binary handling.
- Added privacy and data handling guidance for local observation, minimized model requests, credentials and retention limitations.

### Added

- Standard MIT LICENSE with Copyright (c) 2026 tsluyy26, and the README AI-assisted development disclosure.
- A Windows source-installation guide with explicit unverified clean-install steps and optional model/Live boundaries.
- CONTRIBUTING, SECURITY, CODE_OF_CONDUCT, DISCLAIMER, and AGENTS guidance, plus this changelog and README navigation.

### Changed

- Expanded ignore rules for local environments, credentials/configuration, logs, caches, build outputs, and narrowly scoped temporary observation artifacts, preserving public examples and fixtures.

### Fixed

- The release-copy PowerShell synthetic-output test no longer assumes a project-local .venv interpreter. Its Python caller passes sys.executable explicitly, and the helper validates the file path.
- Added three interpreter-argument regressions while retaining report, exit-code, and token-redaction assertions.
- Explicitly disabled bytecode generation for the fresh Python offline test host.

### Verification Note

After the v0.4.1 development milestone, independent release-copy acceptance initially passed 147 of 148 tests with one portability failure. Following the fix and three additional cases, the latest full run passed 151 tests with no failures, errors, or skips. This later result is not the original v0.4.1 development count. The maintenance-document task did not rerun the functional suite.

## 0.4.1 — Development milestone

### Added

- ProviderConfig, ModelCapabilities, LLMRequest/LLMResponse, registry/manager, and explicit named-provider selection.
- Responses and OpenAI-compatible Chat Completions adapters, plus a trusted Python registration interface for custom adapters.
- Multi-provider TOML validation, capability declarations, and unified output before existing plan parsing/validation.

### Changed

- Kept the legacy OpenAI/Mock API compatible while moving vendor-specific calls behind adapters.
- Reused the existing Scene, prompt projection, plan validation, and rule fallback.

### Fixed

- Isolated mutable request/evidence data from custom adapters.
- Made overlapping model-request log suppression restore state only after the last user exits.

### Security

- Added explicit custom-endpoint confirmation, local-HTTP authorization, TLS verification and redirect/parameter restrictions.
- Rejected secret echoes and unsafe response shapes without switching to another provider.

Real third-party model API compatibility was not established by these changes; SDK contracts were tested with in-memory HTTP.

## 0.4 — Development milestone

### Added

- An optional LLM Planner with deterministic offline Mock and an explicitly selected OpenAI Responses provider.
- Redacted planning input, strict JSON/evidence validation, confirmation items, and rule fallback.

### Changed

- Separated planning status from execution permission in the CLI. A review plan does not grant action authority.
- Retained compatibility for the older rule-planner API.

### Fixed

- Preserved ambiguous/negative-goal handling through the conservative input projection.
- Refused invalid evidence, dangerous suggestions, and output claiming task completion.

### Security

- Required explicit model/data-sharing choices and environment-based keys.
- Treated observed UI text as untrusted and kept screenshots/free-form desktop text out of model input.

No desktop action executor was added. The optional OpenAI Responses provider path was implemented, but no live paid-model API integration was verified in this milestone.

## 0.3 — Development milestone

### Added

- Version-pinned Streamable HTTP client integration with initialization, tool discovery, Bearer authentication, ordered observation, and session cleanup.
- A separate manual PowerShell integration launcher with old-instance/port preflight and owned-process cleanup.
- SDK in-memory HTTP/SSE and script-safety tests.

### Fixed

- Made ambiguous Windows-MCP interpreter discovery stop and request an explicit choice.
- Forwarded sanitized synthetic/report output while keeping exit codes separate.
- Added a bounded 16 MiB SSE event allowance after a synthetic large-event regression and introduced safe diagnostics.

### Security

- Restricted Agent Live endpoints to literal 127.0.0.1, retained the three-tool allowlist, and stopped on controller refusal/errors.
- Kept raw image data out of saved reports and preserved desktop-control protection.

One real Windows-MCP 0.8.8 read-only observation was later confirmed by the maintainer. It did not execute a desktop plan. The earlier field Screenshot failure was not conclusively attributed to one root cause; the synthetic SSE regression and successful retry do not prove that attribution.

## 0.2 — Development milestone

### Added

- Injectable MCP client/transport boundaries, typed failures, timeouts, redacted Replay, and real-result envelope/text adaptation.
- Per-call timestamps, field sources, unknown information, and unassigned controls.

### Changed

- Distinguished historical observations from current control authority.
- Parsed reported capture limits rather than applying the Mock limit to every real response.

### Fixed

- Handled nested JSON text blocks and kept redaction aliases consistent across blocks.
- Avoided creating an event loop for the synchronous offline Replay CLI.

This stage inspected installed 0.8.8 protocol behavior; it did not establish a real standalone HTTP integration.

## 0.1 — Development milestone

### Added

- A standalone standard-library Python project managed with UV.
- Explicitly labeled Mock observations, Scene modeling, a limited Notepad rule planner, and Chinese terminal/JSON reports.
- Conditions, verification and stop rules, evidence sources, incomplete-data handling, and initial offline tests.

No real MCP service, model request, or desktop execution was part of this milestone.

## Historical Verification Records

| Stage | Recorded result, not a release claim |
| --- | --- |
| v0.1 | 17 passing tests |
| v0.2 | 50 passing tests |
| v0.3 | 83 at the final recorded development/integration-fix stage; earlier stages had fewer |
| v0.4 | 117 passing tests |
| v0.4.1 original development | 148 passing tests |
| Later independent release-copy acceptance | 151 passing tests after the portability fix; see Unreleased |

Sources: [v0.1 design](docs/design-and-plan.md), [v0.2 record](docs/v0.2-review-and-design.md), [v0.3 record](docs/v0.3-design.md), [v0.4 record](docs/v0.4-design.md), [v0.4.1 record](docs/v0.4.1-design.md), and the maintainer's internal development/task records. Historical documents retain their original scope; they are not current release announcements.
