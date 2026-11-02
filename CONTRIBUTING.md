# Contributing to Windows Observer Agent

## Introduction

Windows Observer Agent turns Mock, redacted Replay, or explicitly enabled Windows-MCP observations into a Scene Model and a conditional plan. Version 0.4.1 observes and plans only: there is no desktop action executor, visual understanding, or verified wheel/exe distribution.

The project is maintained by tsluyy26. The public repository is [tsluyy26/windows-observer-agent](https://github.com/tsluyy26/windows-observer-agent), with main as its default branch. The maintainer confirmed the first push-triggered Windows CI run passed all 151 offline tests on 2026-11-02. Branch protection and required-review settings have not been verified.

## Ways to Contribute

- Report a reproducible bug using synthetic or redacted data.
- Explain a feature's user benefit, scope, and safety implications.
- Improve installation steps, examples, translations, or explanations.
- Propose a focused code change with appropriate regression coverage.

Do not publish vulnerability details as ordinary bug reports; read [SECURITY.md](SECURITY.md). Community interactions follow [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

## Development Environment

Use Windows 11 for the currently recorded full acceptance environment, Python 3.14.x (>=3.14,<3.15), and preferably the pinned 3.14.10 from .python-version. UV is recommended. PowerShell 7, available as pwsh, is needed for the script tests. Other operating systems have not received a full support claim.

From the source root, after obtaining Python/UV as described in the [Windows guide](docs/installation-windows.md):

~~~powershell
# Creates a local environment; do not run during a read-only review.
uv venv --python 3.14.10 --no-python-downloads

# Base environment: standard-library features.
uv sync --locked --no-python-downloads

# Alternatively, prepare both SDK extras for full acceptance.
uv sync --extra live --extra llm --locked --no-python-downloads
~~~

These are setup instructions, not proof of a clean-machine installation. Sync can download dependencies and remove unselected optional packages. Retain all required extras when syncing. The live extra installs client SDKs, not a Windows-MCP server. The llm extra installs the model SDK; it does not authorize model requests. Do not use an unrelated environment as a sync target.

The package is currently run from the source root, not from an installed console command. No virtual-environment activation is required.

## Running Tests

### Standard-library smoke suite

With an existing suitable interpreter:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_agent.py -v
~~~

This focused suite is not a substitute for full acceptance.

### Full offline suite

After installing live and llm extras and PowerShell 7:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
~~~

The latest recorded release-copy run passed 151 tests with no skips; that is a historical result, not a result from editing these documents. Missing SDKs or pwsh can cause skips. Report passed, failed, errored, and skipped counts separately.

The suite uses Mock data, redacted Replay, fake sessions, and SDK in-memory HTTP transports. It needs no model key or live desktop service. Some tests launch controlled Python/PowerShell test hosts; this does not mean they launch Windows-MCP. Windows event loops may also create internal socketpairs.

The full recorded environment is Windows 11. PowerShell-specific cases need pwsh and exercise script syntax, mocked preflight/cleanup, and synthetic output. They do not read a real desktop. Real Windows-MCP observation is a separate manual integration, never a prerequisite for ordinary test discovery.

If a task permits only inspection, use an already prepared interpreter and check source imports; do not run UV setup or sync. Documentation-only changes can be checked with syntax, link, encoding, and consistency reviews without rerunning the whole functional suite. Real Live/model tests require separate explicit authorization. Do not execute scripts/Test-Live.ps1 as a test shortcut.


### GitHub Actions CI configuration

[Windows CI](.github/workflows/ci.yml) is configured for push, pull_request and manual dispatch on GitHub-hosted Windows. It installs the Python pin and locked live/llm extras with UV, then runs the same unittest discovery as the full local command above. A small result wrapper prints counts and fails on test failures, errors, empty discovery or skips; SDK/Python versions and checkout imports are checked. It does not hardcode the historical 151 count.

Setup downloads tools/dependencies; the test phase uses Mock, Replay and in-memory transports, not live desktop/model services. Mocked PowerShell safety tests may load script functions, but never invoke the real Live launcher entrypoint. No personal API keys or MCP tokens belong in GitHub Secrets for this job. Checkout uses only the platform's read-only repository token, without persisting credentials.

The maintainer confirmed the Initial commit #1 run succeeded: 151 passed, 0 failures, errors or skips (53 seconds). Inspect Windows CI / offline-tests in Actions for each subsequent push and check PR results separately; PR-triggered execution has not been verified by that first push run. A local pass and a green remote check are separate evidence. Branch protection is not configured by this file.

## Coding Guidelines

- Follow the existing small-module, four-space Python style. Prefer clear functions and dataclasses to unnecessary frameworks.
- Add useful type annotations at public boundaries; preserve existing data contracts and conservative unknown values.
- Distinguish observation failure, planning status, and execution permission.
- Handle expected failures with bounded timeouts and safe errors. Do not hide a failed observation as success.
- Keep keys, tokens, raw UI text, provider response bodies, and screenshots out of logs and exception reports.
- Preserve offline defaults, redaction, tool allowlists, and resource cleanup.
- For a bug fix, add a meaningful regression. Keep existing assertions unless their replacement is explained and reviewed.
- Add dependencies only for a demonstrated need; dependency/lock changes require their own review.
- Keep external-code provenance and applicable copyright/license notices. Similarity alone is not proof of copying or originality.

No formatter, linter, or pre-commit configuration is currently enforced by this project; do not claim such checks ran when they did not.

## Pull Request Guidelines

Human contributors may fork the public repository, create a branch, make focused commits, and propose a PR against main. No required-review gate, CLA bot or branch-protection rule is asserted here.

Explain the problem, change scope, validation performed and its limits, documentation updates, and security/compatibility effects. For an API adapter, state the exact SDK/interface and whether evidence is mocked or from an authorized real integration. Do not add personal machine paths or private desktop captures as proof.

The maintainer reserves all Git/GitHub actions performed on their behalf to themselves. This restriction is for AI assistants, not a ban on ordinary human contributors using Git. AI assistants must follow [AGENTS.md](AGENTS.md).

## AI-Assisted Contributions

Codex, Claude Code, GitHub Copilot, and similar tools may help. A contributor must review and understand proposed AI-generated code and remains responsible for what they submit. Do not submit unreviewed generated patches or claim human review that did not happen.

AI tools must not perform desktop actions, submit code, or publish a version without appropriate explicit authority. For this project, the maintainer additionally prohibits AI assistants from executing any Git command or performing GitHub operations on their behalf; the maintainer performs those steps personally.

Never submit real API keys, tokens, screenshots, sensitive logs, personal data, or unredacted desktop records. Respect third-party licenses and identify any substantial copied/adapted implementation. Tool use is not an endorsement or proof of exclusive copyright in every generated line.

## License

The project uses the [MIT License](LICENSE). Contributions must be content you are entitled to provide under that license; third-party components retain their own terms. This document does not establish a CLA or transfer a third party's rights.

## Organizational References

The separation of contribution guidance and user-risk information was informed by [Microsoft UFO](https://github.com/microsoft/UFO). The idea of a concise project-specific instruction file for coding assistants was informed by [MCP Python SDK AGENTS.md](https://github.com/modelcontextprotocol/python-sdk/blob/main/AGENTS.md). These guidelines were written for this project; upstream policies and code were not copied.
