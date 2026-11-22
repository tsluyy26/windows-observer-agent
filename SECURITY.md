# Security Policy

This is a development and pre-release project maintained by tsluyy26. It currently observes and plans; it does not execute desktop actions.

## Supported Versions

| Version | Security maintenance status |
| --- | --- |
| Current v0.4.1 development line | Current review target; no stable-release support or response guarantee |
| Historical v0.1–v0.4 milestones | No backport commitment |

There are no published GitHub Releases or configured security-response SLA. Fix availability and supported versions must be reassessed before a public release.

## Security Scope

Please distinguish vulnerabilities in this project from issues in an independently installed Windows-MCP server or model provider.

Relevant project boundaries include:

- Live MCP accepts an explicitly supplied literal 127.0.0.1 endpoint and reads its Bearer token from WOA_MCP_TOKEN. It does not auto-start a server, scan for alternatives, or fall back to stdio.
- Only ControlStatus, Screenshot, and Snapshot may be called. User takeover, unavailable/unknown control, errors, or refusals stop collection. Live validates the reported state and generation after each capture. A refusal or discontinuity discards provisional data. Closing the client session provides no acknowledgment that a server-side capture worker has stopped.
- Screenshot content is briefly handled in memory and raw image data is omitted by normalization. Snapshot UI/window information may still be sensitive inside process memory.
- Model requests are off by default. Named external providers require explicit enablement and sharing confirmation, a key environment variable, and exact confirmation of custom endpoints. The legacy openai planner uses explicit planner/model selection plus sharing confirmation.
- Model HTTPS uses certificate verification; local HTTP requires specific localhost/127.0.0.1 authorization. MCP Live has the stricter 127.0.0.1 rule. Redirects and environment proxies are not used by the built-in clients.
- Bounded calls, cancellation/cleanup, safe errors, conservative redaction, and untrusted-UI handling are relevant attack surfaces. No filter is a complete semantic security proof.
- Every plan remains non-executable. An observation result or planning_status does not grant desktop permission.

The manual `scripts/Test-Live.ps1` launcher requires a separately authorized session with no conflicting service. Its private Windows Job Object contains the server, probes, observer, and descendants created inside the Job. An unverifiable listener blocks the run. Cleanup reports success after the Job empties, the original process handle confirms root exit, and the selected port has no listener. A forced host exit closes the Job handle and Windows terminates Job members. The interrupted script cannot report cleanup verification. See the [manual Live procedure](docs/installation-windows.md#11-可选真实桌面观察的边界) for failure handling and verification scope.

The manual launcher passes an explicit environment to Windows-MCP and its interpreter metadata probe. The server receives a newly generated Bearer token. Required paths come from Windows, and the allowlist excludes inherited Provider credentials and runtime overrides. See [manual Live child environment](docs/PRIVACY_AND_DATA_HANDLING.md#manual-live-child-environment).

The manual launcher disables the installed Windows-MCP 0.8.8 PostHog path before import with `ANONYMIZED_TELEMETRY=false`. Independently started servers retain their own settings. Upstream exception telemetry can carry sensitive messages and stack paths, including data outside the Agent's report redaction boundary. See [Windows-MCP telemetry](docs/PRIVACY_AND_DATA_HANDLING.md#windows-mcp-telemetry).

The [public error boundary](docs/PRIVACY_AND_DATA_HANDLING.md#public-errors-and-diagnostics) describes fixed categories for handled CLI failures and validated status fields from the manual launcher. Report error fields and model fallback reasons are projected before display. In-process custom adapters still control their direct output and logging.

The [input resource limits](docs/PRIVACY_AND_DATA_HANDLING.md#input-resource-limits) document bounded HTTP and local file reads, session budgets, JSON shape checks, and the SDK and custom-adapter boundaries.

The [disclaimer](DISCLAIMER.md) explains user-facing limitations; it does not replace these technical controls.

## Reporting a Vulnerability

Use GitHub private vulnerability reporting for this repository. Sign in to GitHub, open [Security → Advisories](https://github.com/tsluyy26/windows-observer-agent/security/advisories), then choose **Report a vulnerability** to send the maintainer a private report.

Keep details of unpatched vulnerabilities out of public Issues, pull requests and Discussions. Omit credentials and real desktop captures even from private reports.

## What to Include in a Report

In your private report, include:

- Affected project version, SDK versions, OS, and relevant mode.
- Expected versus actual behavior and the security impact.
- Minimal synthetic or carefully redacted reproduction steps.
- Whether a real service was contacted, and any permissions needed.
- Proposed mitigation, if known.

Use dummy credentials. Omit private UI content, raw provider responses, authorization headers, and personal paths. A reproducible mock is preferable to a sensitive desktop capture.

## Response Process

Once a safe report is received, the maintainer intends to assess scope, attempt an isolated reproduction, consider mitigations, and coordinate a fix and suitable disclosure. This is a proposed best-effort process for one maintainer, not an operating security team or a promised timetable.

There is no guaranteed acknowledgment, triage, or remediation time and no bounty program announced. Do not infer SLA coverage from this file's existence.

## Disclosure Policy

Coordinate publication with the maintainer after safe handling and mitigation have been discussed. Avoid exposing an unpatched exploit or another person's data. This project has not established a fixed disclosure deadline or a formal advisory workflow.

The maintainer handles any later public advisory separately from the initial private report.
