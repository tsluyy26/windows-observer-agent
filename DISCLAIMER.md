# Disclaimer

## General Disclaimer

Windows Observer Agent v0.4.1 is development software for desktop observation and review-only planning. It has not completed clean-Windows installation or wheel/exe distribution acceptance, and it is not a fully autonomous desktop operator.

A plan, a successful tool response, or an exit code is not proof that a user's task was carried out. This document describes limitations; it does not replace consent, access controls, privacy safeguards, or the [security policy](SECURITY.md).

## Desktop Observation

The default source is synthetic Mock data, not your screen. Replay reads a local historical, redacted record and cannot establish the current desktop or current permission.

Only explicitly enabled Live mode connects to a separately prepared Windows-MCP service. ControlStatus reports controller state; Screenshot obtains image content; Snapshot supplies UI-tree/window/display information and may report incomplete observations. The standard Live sequence is ControlStatus, Screenshot, then Snapshot, after initialization and tool discovery.

The Agent currently bases its interpretation on UI structure and tool text, not visual understanding. Screenshot image data is handled briefly in memory and omitted during normalization. Raw window/control text may exist in internal observations while the program processes a Live result.

Not seeing a window does not prove it is absent or its application is uninstalled. Recognizing a control does not verify that it can be clicked. A tree without an explicit truncation marker is not proof of completeness.

The separate manual integration launcher starts a real Windows-MCP test process and later attempts to clean up only its own process tree. It must not be confused with the Agent client, which never auto-starts that service. Existing instances or occupied ports cause the launcher to stop, not terminate unrelated processes. Interrupted runs may need manual ownership checks.

## Privacy and Data Handling

Default rule/Mock and offline Mock LLM/Replay operation makes no model request. The Agent has no default raw-screenshot persistence or raw-desktop export step.

Live CLI reports use a redacted projection. LLM-mode reports and prompts use an allowlisted projection that omits original titles, free-form control text/values, paths, source strings, and raw screenshots. They retain limited structure, aliases/evidence, known application/control categories, timestamps, status, and uncertainty. Goal reduction can remove important meaning; redaction is not a complete privacy or semantic guarantee.

Do not assume every input or terminal output is sanitized: a rule run against your own Mock file can display the content you supplied. Terminal history/buffers, shell redirection, crash dumps, monitoring software, the MCP server, and provider infrastructure are outside this promise of no default Agent file output. Avoid collecting sensitive data in the first place.

Data does not always stay on your computer. After explicit external-provider selection and data-sharing confirmation, the Agent sends the reduced goal and structured observation information to the selected service. Mock-origin data can also be sent if you deliberately enable an external planner. No screenshot upload is implemented by the built-in planner.

## Third-Party AI Providers

An enabled external service may receive data, charge fees, retain requests under its own terms, fail, or return misleading output. Check the exact endpoint, model, account, budget, and provider policies before authorizing a request.

Keys come from environment variables, which are readable in process memory; they are not a secret vault. Do not put real keys or tokens in code, configuration templates, commands saved to history, logs, screenshots, or reports.

Built-in clients enforce endpoint/authentication/TLS rules and bounded requests. These controls are not a guarantee about a provider's conduct. A store=false request option is not a contractual zero-retention promise. Trusted custom Python adapters can have their own side effects and are not sandboxed by registration.

Model plans can be wrong even after schema and evidence validation. Review the goal, uncertainties, conditions, verification, and stop criteria yourself. Rule fallback is not proof that an external model worked.

## Future Automation Features

There is currently no desktop action executor and no automatic Click, Type, keyboard, mouse, PowerShell, or filesystem action driven by a generated plan. All plans have executable=false and execution_permission=not_granted. Read-only observation does not grant action authority.

Vision, controlled execution, recovery, and GUI work are future plans. They require separate design, authorization, safeguards, and validation; this disclaimer does not authorize or promise them.

## User Responsibility

Use observations only on desktops and information you are lawfully authorized to read. Do not use the project for unauthorized surveillance. Ensure appropriate consent and organizational permissions before collecting other people's data.

Choose a controlled environment, protect credentials, avoid sensitive windows, verify service ownership, and review every suggested plan. Do not bypass Windows-MCP controller protection or run conflicting service instances. Confirm cleanup before restoring another desktop-agent service.

## No Warranty

The software is provided under the warranty and liability terms in the [MIT License](LICENSE). No additional guarantee of correctness, suitability, privacy, security, or uninterrupted service is made by this document, subject to applicable law. Third-party components remain under their own terms.

The structure of a separate desktop/data-risk notice was informed by [Microsoft UFO](https://github.com/microsoft/UFO/blob/main/DISCLAIMER.md). This text is written for this project's observation-only behavior; it does not adopt UFO's execution or screenshot-inference policies.
