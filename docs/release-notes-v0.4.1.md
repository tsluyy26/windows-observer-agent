# Windows Observer Agent v0.4.1

## Overview

Windows Observer Agent builds a desktop scene from observations and turns a user goal into a plan for review. Run it from source on Windows. The default workflow uses synthetic data, runs offline and needs no model account.

## Major features

- Use Mock data or replay a redacted historical observation. The rule planner produces preconditions, evidence, verification steps and stop conditions. Every plan is non-executable.
- Explicit Live mode connects to an existing local Windows-MCP 0.8.8 server. Its three allowed tools are ControlStatus, Screenshot and Snapshot.
- Select an offline Mock model, an OpenAI Responses or compatible Chat Completions provider, or a trusted Python adapter. External planning requires explicit selection and data-sharing consent. Provider output is validated locally.

## Security improvements

The manual Live launcher verifies the listener identity, requires rejection of missing and incorrect Bearer tokens, then verifies authenticated initialization and tool discovery. The server and its interpreter probe receive a restricted environment. A Windows Job Object contains the processes launched for the run. Provider configuration rejects the MCP credential variable, and the built-in model path checks requests and decoded responses for credential exposure.

## Privacy and data handling

Replay filters unknown fields and local input paths. Live reports restrict control types to recognized labels. Raw tool data may briefly exist in memory. Screenshots are omitted from reports and model requests. Redacted structural metadata can still identify a desktop. The manual launcher opts out of the audited Windows-MCP telemetry path. Independently deployed services and external providers control their own telemetry and retention.

## Reliability improvements

HTTP bodies, SSE frames and sessions, discovery, local files and JSON structures have explicit limits. Handled failures use bounded diagnostic categories. Live rechecks control state and generation after Screenshot and again after Snapshot. A failed check discards provisional data and stops the observation. Late tool responses after cancellation or timeout are rejected.

## Testing summary

The final recorded local offline run passed **240 of 240** tests with zero failures, errors or skips (35.439 s). The preceding full run had one intermittent S8 Job-count failure. The unchanged PowerShell suite then passed 23/23. The early Windows CI run passed 151/151. These are separate historical runs. No tests were rerun for the freeze.

## Known limitations

- One earlier read-only desktop integration succeeded. This release did not revalidate real Windows-MCP authentication, restricted-environment startup, takeover timing or normal/abnormal shutdown.
- A capture worker already running on the server may finish after user takeover or client cancellation. An observation grants no desktop execution permission. There is no visual understanding or desktop executor.
- Clean Windows 11 setup, a real provider compatibility matrix, and wheel/EXE distribution were not validated. Independent-server telemetry delivery and retention were not measured.
- GitHub private vulnerability reporting was confirmed enabled with a public entry point. The signed-in researcher form, cross-account access and end-to-end delivery were not tested.

## Installation

Extract the source archive, open PowerShell in the folder containing `pyproject.toml`, and use UV with Python 3.14.10. Initial setup may require internet access. For the default Mock workflow:

```powershell
uv python install 3.14.10
uv sync --locked --python 3.14.10 --no-python-downloads
.\.venv\Scripts\python.exe -B -m windows_observer_agent
```

For optional client and model SDKs, use `uv sync --locked --extra live --extra llm`. The Live extra does not install the Windows-MCP server. The GitHub-generated source archive contains source files only.

## License

MIT License. Copyright (c) 2026 tsluyy26. Third-party components retain their own licenses.
