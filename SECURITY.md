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
- Only ControlStatus, Screenshot, and Snapshot may be called. User takeover, unavailable/unknown control, errors, or refusals must stop collection rather than bypass protection.
- Screenshot content is briefly handled in memory and raw image data is omitted by normalization. Snapshot UI/window information may still be sensitive inside process memory.
- Model requests are off by default. Named external providers require explicit enablement and sharing confirmation, a key environment variable, and exact confirmation of custom endpoints. The legacy openai planner uses explicit planner/model selection plus sharing confirmation.
- Model HTTPS uses certificate verification; local HTTP requires specific localhost/127.0.0.1 authorization. MCP Live has the stricter 127.0.0.1 rule. Redirects and environment proxies are not used by the built-in clients.
- Bounded calls, cancellation/cleanup, safe errors, conservative redaction, and untrusted-UI handling are relevant attack surfaces. No filter is a complete semantic security proof.
- Every plan remains non-executable. An observation result or planning_status does not grant desktop permission.

The separate scripts/Test-Live.ps1 launcher can start and clean up a real test server. It is not an ordinary test entrypoint and requires a separately authorized, controlled session without a conflicting existing service.

The [disclaimer](DISCLAIMER.md) explains user-facing limitations; it does not replace these technical controls.

## Reporting a Vulnerability

**No formal private security reporting channel is configured yet.** There is no security email, project repository reporting URL, or enabled GitHub Private Vulnerability Reporting to offer here.

Do not put exploit details, credentials, real screenshots, or unpatched vulnerability specifics in a public Issue, PR, discussion, or chat transcript intended for publication. If you already have a verified private contact with the maintainer, ask whether it is suitable before sending a report; this document does not establish such a channel. Otherwise, wait until a private channel is published rather than disclosing sensitive details publicly.

The repository is now public, but a private reporting channel is still unverified and remains an outstanding maintainer task. Configure and test a private reporting method and update this section. It may be GitHub private reporting or another appropriate mechanism, but neither is currently asserted to exist.

## What to Include in a Report

When a private route has been established, provide:

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

Maintainer setup still required: a private reporting route, its visibility/access checks, and a documented disclosure/release process. Ordinary contribution instructions must not be used to publish vulnerability details.
