# Windows Observer Agent

[中文](README.md) | [English](README.en.md)

Windows Observer Agent is a standalone Python program that turns desktop observations into a structured Scene Model and a conditional plan with prerequisites, verification steps and stop conditions. **Version 0.4.1 observes and plans; it does not execute desktop actions.**

Start offline with bundled Mock data, without Windows-MCP, a model account or an API key. For live observation, explicitly connect to an already prepared local Windows-MCP HTTP service. Optional model planning supports Responses, OpenAI-compatible Chat Completions and adapters registered through trusted Python code.

Repository: [tsluyy26/windows-observer-agent](https://github.com/tsluyy26/windows-observer-agent), default branch main. The detailed [Windows installation guide](docs/installation-windows.md) is currently in Chinese; the source setup below is provided in English.

## Features

- Separate acquisition, transport, adaptation, Scene, planning and reporting modules.
- Mock, sanitized local Replay and explicitly enabled Live observation sources.
- Vendor-neutral provider requests/responses, specific SDK adapters and multi-provider configuration; no arbitrary Python imports from configuration.
- Standard-library offline rule planning and deterministic Mock LLM planning.
- Local plan schema, evidence and safety validation, with rule fallback rather than an automatic provider switch.
- Planning status separated from execution authority: every plan remains executable=false and execution_permission=not_granted.

The rule planner handles a limited family of requests such as creating a blank Notepad document. Mock LLM is a test double, not general language understanding. Goal reduction uses a small, primarily Chinese task vocabulary and can discard meaning. An English README does not add English CLI localization or general English-goal support; the examples retain a supported Chinese goal.

## Current status: v0.4.1

| Area | Evidence and limits |
| --- | --- |
| Offline tests | The maintainer confirmed the first push-triggered GitHub Windows CI run on 2026-11-02 passed 151 tests, with 0 failures, errors or skips. This documentation task did not rerun tests or CI. |
| Real desktop observation | One Windows 11 / Windows-MCP 0.8.8 read-only integration succeeded, calling ControlStatus → Screenshot → Snapshot. |
| Model adapters | Responses / Chat / Custom architecture and in-memory SDK tests exist; full compatibility with real third-party model APIs has not been verified. |
| Images | Image blocks can be received and processed in memory. No visual understanding or default raw-image saving/upload is implemented. |
| Installation and distribution | Run from the source root. Clean-Windows user installation, wheel, pip installation and exe distribution have not completed acceptance. |
| Desktop execution | No action executor; plans cannot click, type or execute suggested commands. |

Public source availability does not imply a formal GitHub Release or a published wheel/exe.

## Requirements

- **Windows 11** is the recorded real-observation platform; no wider OS support matrix is claimed.
- **Python 3.14.x**: pyproject.toml requires >=3.14,<3.15; .python-version pins 3.14.10.
- **UV** is recommended for Python environments and locked dependencies, not a runtime library. CI pins UV 0.13.6; local source setup does not impose a UV version through project metadata.
- **PowerShell 7** is required for script tests and the manual integration launcher.
- **Windows-MCP 0.8.8 is optional** and separately installed. Mock and Replay do not require it. The live extra installs client SDKs, not the server.
- Network access is needed to download tools/dependencies. An external model additionally requires an account/key, cost acceptance and explicit data-sharing consent. Prepared offline modes need none of these model services.

## Install and run from source

These instructions follow the checked configuration, but have not been accepted as a complete clean-Windows installation procedure. They create an environment and may download files. They were not executed for this README task.

Obtain the source from the public repository using Code → Download ZIP, or your own Git client. Do not copy another user's virtual environment, private configuration or desktop captures.

Install UV if needed, then reopen PowerShell:

~~~powershell
winget install --id=astral-sh.uv -e
uv --version
~~~

Choose the source directory containing pyproject.toml:

~~~powershell
$project = Read-Host 'Path to the project source root'
Set-Location -LiteralPath $project
Test-Path -LiteralPath .\pyproject.toml
~~~

Prepare Python and the base environment:

~~~powershell
# Requires network; skip if the pinned interpreter is already available.
uv python install 3.14.10
uv venv --python 3.14.10 --no-python-downloads
uv sync --locked --no-python-downloads
~~~

For full SDK tests or both optional interfaces, use this sync selection instead:

~~~powershell
uv sync --extra live --extra llm --locked --no-python-downloads
~~~

Base dependencies are empty. The live extra pins mcp==2.3.0 and httpx2==2.16.0; llm pins openai==3.42.0. Sync can install packages or remove extras not selected in a later sync. --no-python-downloads prevents interpreter downloads, not dependency downloads.

UV run may sync the environment automatically; it is unsuitable for read-only acceptance. The project currently has no verified pip installation or installed command entrypoint. Run from the source root using its interpreter; environment activation is unnecessary. See the [installation guide](docs/installation-windows.md) for setup and troubleshooting.

## Offline usage

Run these commands from the source root after preparing the environment:

~~~powershell
# Default Mock observation and rule plan; does not read the desktop.
.\.venv\Scripts\python.exe -B -m windows_observer_agent

# Offline deterministic model substitute; no key or model request.
.\.venv\Scripts\python.exe -B -m windows_observer_agent --planner mock-llm --goal '新建一份空白记事本文档。'

# Local provider configuration validation; no key, connection or observation.
.\.venv\Scripts\python.exe -B -m windows_observer_agent --provider-config .\examples\providers.toml --validate-provider-config

# Select the named offline adapter.
.\.venv\Scripts\python.exe -B -m windows_observer_agent --planner provider --provider-config .\examples\providers.toml --provider offline

.\.venv\Scripts\python.exe -B -m windows_observer_agent --help
~~~

The CLI and reports are currently primarily in Chinese. Add --json for structured report output. --mock-file, --replay-file and --live are mutually exclusive; --endpoint must accompany --live.

The supplied sanitized historical fixture records user control and tool failure. This replay is expected to exit with code 1:

~~~powershell
.\.venv\Scripts\python.exe -B -m windows_observer_agent --replay-file .\tests\fixtures\windows_mcp_0_8_8_redacted.json
~~~

Outside Live mode, exit 0 means a conditional plan, 1 means confirmation/unsupported/failed planning, and 2 means a configuration or read error. Live exit 0 only means observation completed; it does not prove model success or task execution. Offline CLI arguments were checked previously, not rerun for this documentation change.

## Model providers

The default planner is rule; there is no default paid model. [examples/providers.toml](examples/providers.toml) selects offline by default.

| Adapter | Purpose | Requirements |
| --- | --- | --- |
| mock | Deterministic offline substitute | No key or SDK |
| openai_responses | Responses API | llm extra, explicit model, endpoint, capabilities and key environment variable |
| openai_chat_completions | OpenAI-compatible Chat Completions | Actual endpoint/model compatibility; not a promise of universal provider support |
| Custom | Trusted Python adapter registration | complete(request) → LLMResponse; no dynamic module import from TOML |

A provider names a service/model/capability configuration. An adapter translates between its interface and the common request/response contract. Configure separate named entries to switch models; --model cannot override a named provider.

This is a fictitious configuration, not a working service:

~~~toml
[llm]
provider = "my_service"
adapter = "openai_chat_completions"

[llm.providers.my_service]
base_url = "https://api.example.com/v1"
model = "example-model"
api_key_env = "EXAMPLE_MODEL_KEY"
timeout = 30
max_output_tokens = 4000

[llm.providers.my_service.capabilities]
text_input = true
chat_completions_api = true
json_mode = true
~~~

Save your own configuration as providers.local.toml and replace the service/model placeholders with verified details. Never place the real key in TOML. Ignoring *.local.toml is not a substitute for protecting secrets.

For Responses, select openai_responses and declare responses_api=true. Set structured_output and json_mode according to the actual model/interface capabilities; both default to false. With structured_output=true, the adapter requests strict JSON Schema. Otherwise, json_mode=true requests a JSON object. If both are false, it requests text JSON and still applies strict local parsing and plan validation. If both are true, structured_output takes precedence. These are implemented client request branches, not a guarantee of service/model compatibility. Do not infer capabilities from a vendor name. The built-in request path remains text-only, with no image input, tool execution or streaming.

Named external providers require --enable-external-model and --confirm-data-sharing. A custom endpoint additionally requires --confirm-provider-endpoint matching the full base_url. Only the standard https://api.openai.com/v1 endpoint is exempt from that additional endpoint confirmation. Remote services require HTTPS with certificate verification. Local HTTP is limited to explicitly authorized localhost/127.0.0.1 services and requires --allow-local-http. Redirects and environment proxies are disabled.

Keys are read from api_key_env. Use the [guide's hidden-input and cleanup example](docs/installation-windows.md#8-配置模型-provider), never a literal key saved in command history. Requests may incur costs and have not completed real third-party compatibility acceptance; they are not part of the offline quick start.

The Chat adapter sends max_tokens. Services requiring a different budget field, authentication or request format need a dedicated adapter. extra_params cannot override protected authentication, model, messages, tools, format or endpoint fields; do not put personal information in extra values. All outputs become LLMResponse before local plan validation. Providers are not silently switched.

The legacy --planner openai path remains available for the official Responses endpoint. It requires --model, --confirm-data-sharing and WOA_LLM_API_KEY, but not the named-provider --enable-external-model flag; it does not support custom base_url. Prefer named configurations for new integrations.

## Architecture

~~~text
Mock / Replay / explicit Live
          ↓
Observation → Scene Model
          ↓
Rule Planner or reduced prompt → Provider Manager → Adapter
          ↓
Local plan validation / safe fallback → review-only Plan → Reporter
~~~

~~~text
windows-observer-agent/
├── README.md
├── README.en.md
├── pyproject.toml / uv.lock / .python-version / .gitignore
├── windows_observer_agent/
│   ├── __main__.py / models.py
│   ├── mcp_client.py / transports.py / mcp_adapter.py
│   ├── observer.py / live.py / replay.py / control_gate.py
│   ├── scene_model.py / planner.py / reporter.py
│   ├── planning_input.py / llm_planner.py / llm_provider.py
│   ├── providers.py / provider_adapters.py
│   └── mock_data/
├── tests/ (Python/PowerShell mocks and sanitized fixtures)
├── examples/providers.toml
├── scripts/Test-Live.ps1 / live-server.toml (manual real integration only)
└── docs/ (Windows installation guide and design records)
~~~

| Modules | Responsibility |
| --- | --- |
| mcp_client, transports, mcp_adapter | Tool policy, connection lifecycle and conservative result adaptation |
| observer, live, replay, control_gate | Acquisition, historical records and controller checks |
| models, scene_model | Structured observations, evidence, unknowns and truncation |
| planner, planning_input, llm_planner | Rules, reduced model input, parsing and validation |
| providers, provider_adapters, llm_provider | Provider contracts, registry and SDK/legacy/Mock implementations |
| reporter, __main__ | Reports and CLI |

Source lives in windows_observer_agent/; tests/ contains Python and mocked PowerShell tests plus sanitized fixtures. examples/ holds public configuration. docs/ contains design and installation records; scripts/Test-Live.ps1 is manual integration tooling.

Not observing a window does not prove it is absent. Recognizing a control does not prove it is clickable. See the [v0.4.1 design](docs/v0.4.1-design.md), [v0.4 design](docs/v0.4-design.md), [Live design](docs/v0.3-design.md) and [0.8.8 protocol notes](docs/windows-mcp-0.8.8-protocol.md). Historical records describe their own version scope.

## Tests and Windows CI

Install both extras and PowerShell 7 before full offline acceptance:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
~~~

The recorded suite has 151 tests. Missing SDKs or pwsh can cause skips; inspect the counts rather than accepting any OK result as full acceptance. SDK tests use in-memory HTTP; PowerShell tests use mocks and synthetic output. They do not invoke the real Live launcher entrypoint.

[Windows CI](.github/workflows/ci.yml) runs the same discovery on a GitHub-hosted Windows runner and rejects failures, errors, empty discovery and skips. Its first push-triggered 151/151 success is a historical result confirmed by the maintainer, not a new run for these READMEs. No claim is made here about branch protection or verified PR-trigger behavior.

## Safety and privacy

- Live connects only to an explicit 127.0.0.1 endpoint using WOA_MCP_TOKEN. The Agent does not start a server, scan alternatives or fall back to stdio.
- Only ControlStatus, Screenshot and Snapshot are allowed, in sequence. Controller refusal, user takeover or errors stop observation; protection is not bypassed.
- The manual scripts/Test-Live.ps1 can start and clean up a real test server. It requires a separate controlled session, checks existing instances, and must not be confused with ordinary startup or offline tests. See the [Live boundary](docs/installation-windows.md#11-可选真实桌面观察的边界).
- No raw screenshot upload is implemented by the built-in planner. Explicitly authorized external models receive a reduced goal and allowlisted structured data rather than original titles, paths, free UI text or images.
- Raw tool text can still exist in memory. Live report redaction and model-input projection differ; structural metadata such as time, application categories, coordinates, dimensions and relationships can still identify activity. Terminal capture, custom inputs/adapters and third-party retention need separate care; do not assume all original text is erased or nothing is persisted.
- External providers may log, retain or process requests under their own policies. The project cannot control internal provider retention; store=false is a request option, not a guarantee of zero retention or no training. Authorized external-provider requests send data off the machine.
- UI text is untrusted data. Local checks reduce risk but do not prove complete semantic safety or model correctness. Registered custom Python adapters are not sandboxed.
- Keep real keys, MCP tokens, screenshots and sensitive records out of public contributions. Ignore rules cannot revoke leaked keys or erase committed history.
- A successful observation or plan never authorizes desktop execution.

Read [Privacy and Data Handling](docs/PRIVACY_AND_DATA_HANDLING.md), [Disclaimer](DISCLAIMER.md) and [Security policy](SECURITY.md).

## Roadmap — not yet implemented

- v0.4.2: authorized real-model integration with explicit endpoint, cost and sharing decisions.
- v0.5: visual understanding with separate image-privacy controls.
- v0.6: controlled execution, step-by-step authority, refreshed observations and verification.
- Later: GUI, recovery, clean-Windows user installation and distribution acceptance.

Offline Windows CI has already completed its first remote acceptance; it is not a future feature. The source repository is public, but a formal GitHub Release and wheel/exe distribution are not established by source publication.

## Documentation and project policies

- [Windows installation guide — Chinese](docs/installation-windows.md)
- [Contributing](CONTRIBUTING.md)
- [Changelog](CHANGELOG.md)
- [Security policy](SECURITY.md)
- [Code of conduct](CODE_OF_CONDUCT.md)
- [Disclaimer](DISCLAIMER.md)
- [AI assistant instructions](AGENTS.md)
- [Privacy and Data Handling](docs/PRIVACY_AND_DATA_HANDLING.md)

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.

Third-party dependencies and components remain subject to their respective licenses and terms.

## AI-Assisted Development

Windows Observer Agent is an independently initiated project designed and maintained by **tsluyy26**.

OpenAI Codex has been extensively used as an AI-assisted development tool throughout the development of this project, including code generation, implementation, refactoring, testing, and documentation.

The project's overall direction, functional requirements, architectural decisions, and development priorities are determined by the maintainer.

The use of AI-assisted development does not imply endorsement by OpenAI or transfer of project ownership to OpenAI.
