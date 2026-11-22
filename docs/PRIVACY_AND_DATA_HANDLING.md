# Privacy and Data Handling

## Overview

Windows Observer Agent v0.4.1 observes and generates plans for review. Its CLI defaults to bundled synthetic Mock data and the rule planner, requiring no model key, desktop access, or network request. There is no desktop action executor or visual image analysis.

This document explains the built-in CLI and adapters as inspected on 2026-11-02. It is not legal advice or a replacement for third-party privacy policies. Custom Python code and independently operated services have their own behavior.

## Data Categories

All data actually read is processed in memory. The application does not provide a default file-writing step for these observations or plans; existing inputs remain on disk and terminal output can be retained separately.

| Category | Origin and potential sensitivity | When read / model treatment |
| --- | --- | --- |
| Desktop images | Screenshot image blocks; may show any visible private content | Explicit Live only; image blocks are omitted during normalization, not sent by the built-in LLM planner |
| Window information | MCP window titles, state, application information; may identify documents or people | Live or local input; original titles excluded from prompts, only known app categories and active flags retained |
| UI information | Control names, types, hierarchy, text, values and coordinates | Live or local input; prompts retain limited categories, safe labels, aliases, parent relationships and selected booleans |
| Controller state | ControlStatus or historical/mock records: ready, ai, user, unavailable and other states | Used to gate Live observation; reduced status flags can enter prompts |
| User goal | CLI argument, possibly containing personal text | Rule planning can retain it in reports; LLM path reduces it to a small task-word vocabulary |
| Model configuration | Local TOML, selected provider/model, endpoint, capabilities, extra parameters | Read for explicit provider use or local validation; model name and allowed extra parameters enter requests, endpoint determines destination |
| Credentials | Model key and MCP Bearer token from separate environment variables | Explicit network modes only; used for authentication, not intended as prompt data; remain accessible in process memory |
| Historical inputs | User-selected Mock or Replay JSON | Read locally; may contain personal information despite labels; a selected external planner can send their reduced projection |
| Reports and model output | Generated analysis, validated plan, safe error categories and usage counters | In memory and stdout/stderr; terminal capture or user redirection can persist them |

Not every field a server returns becomes a recognized Scene field. Unrecognized data can still exist in the received payload. An omission from the prompt is not proof that the information never entered memory.

## Operating Modes and Data Flows

### A. Default Mock / Rule Planner

The CLI selects rule by default. MockClient reads mock_data/normal.json unless a local Mock file is selected. observe and build_scene construct the model, make_plan creates a review-only plan, and Reporter prints it. This path does not contact MCP or a model and does not read the screen.

Custom Mock data and the original goal can appear in rule reports, including JSON output. Offline does not mean safe to publish.

### B. Offline Mock LLM

The mock-llm planner uses MockLLMProvider, a deterministic local response generator. It needs no key or external service. It receives the same reduced prompt used for model planning; the local parser validates its plan. CLI output uses public_planning_scene. This does not verify a real model's privacy or compatibility.

### C. Replay Mode

ReplayClient reads a local `woa.replay.v1` JSON file and requires `redacted: true` on the file and each record. It checks tool sequence, arguments and result objects, then builds the historical observation from an allowlisted projection. Text and JSON reports use that projection, including any records in the raw observation section. The projection replaces titles, handles and control names with aliases or placeholders, normalizes unknown control types, omits image bytes and extra fields, and uses a fixed source label in place of the local path. Timestamps, display geometry, recognized control types and historical status can remain. Invalid files are rejected with a generic Replay error.

The original file is read into memory. The redaction flags are input markers and the file is left on disk as supplied.

Replay does not observe today's desktop or grant current control. With rule or Mock LLM it is offline. Selecting an authorized external provider sends the reduced historical goal/Scene projection to that provider.

### D. Windows-MCP Live Mode

Live requires --live and an explicit --endpoint. StreamableHTTPTransport accepts a literal 127.0.0.1 host, validates its URL, and reads WOA_MCP_TOKEN (at least 32 ASCII characters without whitespace). It does not scan for a server, start one, or fall back to stdio.

After discovery the client calls `ControlStatus → Screenshot → ControlStatus → Snapshot → ControlStatus`. A detected takeover, unexpected generation change or failed check discards the observation and stops further collection. Checks sample ownership at specific instants. A timeout or cancellation can end the wait while work already running on the server may still finish. Screenshot uses `use_annotation=false`. Snapshot uses `use_vision=false`, `use_dom=false` and `use_ui_tree=true`. Only `ready` and `ai` with a valid generation and zero wait may proceed. Across a capture, the client accepts an unchanged state and generation or the normal `ready` to `ai` lease with exactly one generation increment. Provisional tool data can enter client memory before the recheck. Discarding it prevents downstream use and does not erase server-side data or guarantee memory wiping. The timing checks have been exercised with synthetic data. Real takeover timing remains unverified.

Screenshot obtains screen image content. normalize_result replaces image content blocks with MIME/omission metadata. This is not secure memory erasure, a general-purpose sanitizer for every unknown field, or visual understanding. Snapshot text may contain sensitive window/control data; raw tool text remains in internal Observation records.

The client uses bounded connection/call/close operations (MCPClient defaults: 5/30/5 seconds), async context cleanup and cancellation handling. Model planning follows closure of the Live session. No automatic tool retry or takeover is implemented. HTTP redirects and environment proxies are disabled.

The separate manual Test-Live.ps1 launcher can start a real test server and manage its own processes; it is not the ordinary Agent client or offline test path. Its behavior and cleanup require a separately authorized integration session.

### E. External LLM Provider Mode

Named providers require --planner provider, a configuration/selection, --enable-external-model and --confirm-data-sharing. Keys come from api_key_env. Non-default endpoints require --confirm-provider-endpoint to match the configured base URL; only the standard official OpenAI base URL is exempt from that extra confirmation.

HTTPS verifies certificates. HTTP is limited to explicitly authorized localhost or 127.0.0.1 model services and additionally requires --allow-local-http. This does not relax Live MCP's literal 127.0.0.1 rule. Redirects and environment proxies are disabled.

The legacy --planner openai path explicitly selects the official Responses service using --model, --confirm-data-sharing and WOA_LLM_API_KEY; it does not use the named-provider enable flag.

Built-in model calls have a configurable positive timeout up to 120 seconds, default 30, with a connect limit of at most 5 seconds. SDK max_retries=0 disables automatic retries. Failures produce safe categories and rule fallback, not a switch to another provider.

#### Exact built-in planning payload

build_prompt sends instructions and the following JSON data, alongside a plan schema:

- goal: reduced task words, not the original arbitrary goal.
- source_kind, observed_at, observation_success, observation_complete.
- ui_tree_truncated and input_truncated.
- control: owner, state, query_success, historical, has_error.
- elements: W/C aliases with kind and evidence alias. Windows add an allowlisted app or unknown and active. Controls add parent, allowlisted type or unknown, allowlisted label or redacted, click_verified=false and only boolean enabled/visible/content_observed when available.
- evidence: E aliases with element, basis=observed-ui-structure and source_kind.
- uncertainties: generated explanations, not raw error/unknown strings.
- privacy flags goal_reduced and free_text_omitted, goal_requires_confirmation, and execution_permission=not_granted.

Projection considers at most 30 windows and 200 controls; omitted data and incomplete observations affect uncertainty. Goal reduction examines at most 4,000 characters and retains at most 40 recognized task words. It can lose important meaning.

Original window titles, arbitrary control text/values, local source paths, raw source strings, coordinates, the full UI tree and image bytes are not included in this prompt projection. Recognized generic labels such as New/File and application categories can remain. No image input is constructed.

Responses sends serialized data in input, instructions/schema, model, output format/budget, no tools, stream=false and store=false. Chat Completions sends system/user text messages, model, format/budget, stream=false and store=false; tool outputs are rejected. Both also send configured extra_params after validation. Those user-supplied values are not desktop redaction output: do not put personal information or credentials in them. Authentication headers and normal connection metadata reach the chosen service separately from the prompt.

## Manual Live Child Environment

The manual launcher supplies a fresh environment block to the Windows-MCP server and its version probe. Windows supplies the system directory, user profile, and local application-data paths. The launcher derives the Temp path under local application data. These paths can identify the local account and are not printed as an environment dump. The probe receives only the six Windows path variables. The server also receives fixed settings and the fresh, run-specific authentication token. The launcher supplies the token in the environment and keeps it out of command-line arguments.

The manual server environment excludes the parent shell's Provider credentials, including custom names, along with proxy, Python, and debug overrides. The launcher sets FastMCP's dotenv path to the Windows null device. A missing required folder or a failed native launch stops the operation. See the [Windows installation guide](installation-windows.md#11-可选真实桌面观察的边界) for the exact environment and verification scope.

This policy covers the server and its version probe started by this script. The Agent and custom Adapters retain control of their own environments and subprocesses. Windows-MCP and its descendants run under the user's account. Any process that deliberately reads other sources of configuration remains responsible for that access.

## Windows-MCP Telemetry

The Agent does not initialize its own telemetry. The manual launcher sets `ANONYMIZED_TELEMETRY=false` before starting Windows-MCP 0.8.8. The upstream lifecycle reads the string case-insensitively and expects no surrounding whitespace. An absent or invalid value takes the enabled branch. In the installed version's controlled lifecycle, the exact `false` setting prevented creation of the PostHog client, access to its identifier file, event creation, and submission to the HTTP adapter.

When enabled and an analytics project key is available, upstream startup reads or creates `~/.windows-mcp/.windows-mcp-user-id`. The file contains a UUIDv7 rather than a username. Its reuse links activity from the same Windows profile, and disabling telemetry leaves an existing file in place. The SDK's uncaught-exception hooks can include exception messages and stack-frame paths. In a separate test of the explicit tool sender, the error message retained synthetic credentials and paths. Captured tool metadata also included the tool name, duration, client and runtime versions, and event timing.

Under the tested fresh startup order, the three allowed tool handlers did not create PostHog events. Screenshot bytes, Observation data, UI text, window titles, and tool arguments did not enter the controlled successful tool sender. Exception messages or client-supplied metadata can still contain sensitive information when telemetry is enabled.

An independently launched service controls its own settings. FastMCP 4.1.4 also has separate OpenTelemetry instrumentation. The tested installation had no configured exporter, and a controlled span was non-recording. The audit intercepted prepared HTTP requests in memory and did not run a real service or establish external traffic or retention behavior. See the [manual Live procedure](installation-windows.md#11-可选真实桌面观察的边界).

## Public Errors and Diagnostics

The project CLI maps handled failures to a stage and a fixed category. File failures report what to check without returning a local path. Configuration, credentials, authentication, connection, tool, timeout, resource, and unexpected failures use separate diagnostics. Provider request failures may produce a rule fallback with a fixed reason code. Live error fields retain approved categories, tool names and limited protocol metadata. Local Mock error fields use fixed labels. This boundary covers handled errors in both text and JSON reports.

The manual Live launcher validates the child process summary and any error category before generating its own terminal text. Child output, exception chains, and native error records may contain private details and are not forwarded as diagnostic prose. The launcher still reports authentication stages and cleanup outcomes. The standard workflow creates no exception log.

This boundary applies to project-controlled errors. User-authored goals and other ordinary report data have their own rules, and an in-process custom Adapter can emit directly to streams, configure its own logging, or create its own files. Host diagnostics emitted before the script starts are controlled by the host.

## Input Resource Limits

The Agent applies the following local limits. MiB and KiB use powers of 1024.

| Input | Limit | Enforcement |
| --- | --- | --- |
| MCP ordinary HTTP response | 16 MiB per body | An HTTPX2 response hook checks a usable Content-Length and stops reading when actual bytes cross the limit. |
| MCP SSE | 16 MiB per framed event, including line delimiters | The response stream checks each frame before the SDK consumes it. The SDK's existing 16 MiB event limit also stays enabled. |
| MCP session | 64 MiB of response body bytes, 1,024 nonempty SSE frames and 128 HTTP requests | One budget covers the entire session, including SSE reconnects. Frames containing only comments also count. |
| Tool discovery | 128 tools per page, 512 tools total and 20 pages | Each SDK-decoded page is checked before tools are copied to the combined list. Duplicate names and invalid or repeated cursors are rejected. |
| Local Mock and Replay files | 16 MiB per file | The Agent checks the opened file handle and reads at most the limit plus one byte. |
| Built-in model response | 1 MiB per HTTP body and 64 KiB of UTF-8 model text | The HTTP limit applies during reading. The text limit applies after extracting the model text from the bounded response. |
| JSON shape | Depth 64, 1,024 fields per object, 16,384 elements per array, 16 MiB per encoded string and 100,000 nodes including keys | A lexical check runs on bounded raw bytes before object creation in covered JSON paths. The JSON parser and SDK still validate syntax and structure. |

The 16 MiB MCP limits retain room for base64 image events larger than the SDK's old 1 MiB default. The model body budget leaves room for the protocol envelope and JSON escaping around 64 KiB of UTF-8 plan text. The largest bundled Mock/Replay fixture is 2,158 bytes. The file budget allows larger history files. The tool limits allow substantially more discovery entries than the Agent's three callable tools.

The built-in HTTP clients request identity encoding and reject compressed responses before reading the body to avoid decompression amplification. A missing, understated or invalid Content-Length cannot disable actual byte accounting. Ordinary JSON bodies and SSE frames may still be buffered up to their limits for shape checks. These limits do not control transport chunks already allocated below the HTTP hook, headers, SDK object copies or server-side work. Connection and call timeouts still apply. If an MCP budget is exhausted, further requests in that session are blocked, including session termination requests. Local resources close and the server may retain state until its own expiry.

Resource violations use fixed categories and stop the affected read. Built-in model HTTP and shape violations flow to the local rule fallback. Local file errors omit the path and input content. Registered custom adapters manage their own network and resource usage.

## Data Redaction and Minimization

Raw MCP normalization removes recognized image blocks, but preserves text and may preserve unknown fields. Scene construction is not global anonymization.

Live rule reports use the same redacted observation for text and JSON, including retained records in the JSON output. Titles and handles become aliases and control names are replaced. Control types use a fixed allowlist of recognized English and Simplified Chinese labels. Case and whitespace are normalized. Unsupported or missing types in recognizable control rows become `unknown`, and unrecognized rows are omitted. Coordinates, dimensions, status, timestamps and capture-limit information can remain.

LLM planning uses the narrower build_prompt allowlist above. public_planning_scene supplies the corresponding reduced report Scene, including reduced raw metadata. It is a different projection from Live rule reporting.

Reporter renders the supplied Scene and Plan; it is not a universal sanitizer. In Live rule mode the plan is made from the internal Scene before the public Scene is substituted. Do not treat report redaction as a guarantee covering every future plan field, custom input or exception. Goals, local input paths or parsing errors can appear in output depending on mode/error path.

Replay sanitization does not retroactively sanitize the input file. Review files before sharing them. Time, app categories, relationships, coordinates and their combinations may still identify activity. Untrusted UI text is data, not instructions; local validation is not a complete semantic security proof. Custom registered Python adapters are trusted code, not a sandbox.

## Local Storage and Retention

Inspection of the application acquisition, planning, adapters and reporting paths found no default screenshot writer, UI-body archive, application log-file handler, Replay exporter, observation cache or crash-dump writer. Replay and Mock paths read existing files; CLI text/JSON is printed rather than saved to an application output file. Sanitization helpers return data and do not automatically persist it.

This scope excludes deliberate shell redirection, terminal scrollback/history, user-created Replay files, Python bytecode caches, operating-system crash/paging/monitoring facilities, independent Windows-MCP storage and provider infrastructure. Manual scripts and test hosts are separate from ordinary observation/reporting. Use -B/PYTHONDONTWRITEBYTECODE for bytecode control when needed; neither clears sensitive memory or terminal history.

Closing resources does not promise a fixed memory-retention interval or secure erasure. The Agent cannot remove copies retained by other software.

## Third-Party Data Processing

External providers can receive reduced observations and goals, request configuration, authentication and connection metadata. They may log requests, retain data, or apply different training policies, fees and rate limits. Check the exact service and account terms.

The Agent cannot control their internal retention. store=false is a request option, not proof of contractual zero retention or no training. No uniform third-party privacy policy or complete real-provider compatibility has been validated. Model output can be wrong; schema validation and fallback do not establish correctness.

## API Keys and Authentication

Model API keys authenticate the selected model service. `WOA_MCP_TOKEN` authenticates the local MCP session and is rejected as a model credential variable, regardless of case. Named model keys are read from the configured environment variable. The legacy model key is `WOA_LLM_API_KEY`.

Built-in SDK adapters check the serialized HTTP request body before sending it. The selected model key is expected only in the authentication header. The adapters and external Provider manager reject a response if that key appears in raw text or decoded JSON fields, including nested objects and arrays. The plan parser rejects terminal control characters in decoded plan text. These checks cover the configured model key and do not detect arbitrary secrets supplied by a user or a provider.

Never put real credentials in source, public TOML, extra parameters, saved commands, logs or screenshots. Use a trusted interactive secret-input mechanism rather than typing a literal key into saved PowerShell history. Environment variables still expose secrets to the process and inherited environments; they are not an encrypted secret vault.

Built-in model log suppression and safe failure conversion reduce disclosure. They do not cover arbitrary custom adapters, independent server logs, operating-system inspection or every surrounding tool.

## User Controls and Recommended Practices

1. Start with bundled Mock data and offline planners.
2. Before Live observation, close password, financial and private-chat windows and obtain authority to read the desktop.
3. Check the selected provider, model, exact endpoint, sharing choice, fees and retention policy.
4. Avoid sensitive goals and configuration values; review reduced output before sharing reports.
5. Protect both credentials and keep real captures out of public contributions.
6. Do not run unknown custom adapters or bypass controller protection.
7. Use the project only with legitimate authorization, never for unauthorized surveillance.

All plans remain executable=false with execution_permission=not_granted. Reviewing a plan does not authorize desktop actions.

## Known Limitations

Redaction is imperfect and can remove task meaning while leaving identifying structure. Visual image analysis and a desktop action executor are not implemented. Custom Python adapters are not sandboxed. Model output is not guaranteed correct. Third-party privacy policies differ, and the Agent cannot control all operating-system or external-service retention.

This is a source-based explanation of current built-in behavior, not a clean-install, live-service or legal certification.

## Related Documents

- [README](../README.md)
- [Disclaimer](../DISCLAIMER.md)
- [Security policy](../SECURITY.md)
- [Contributing](../CONTRIBUTING.md)
- [MIT License](../LICENSE)

## Source Map

| Sections | Implementation evidence |
| --- | --- |
| Overview and modes | [CLI](../windows_observer_agent/__main__.py), [Mock/client policy](../windows_observer_agent/mcp_client.py), [observer](../windows_observer_agent/observer.py) |
| Live acquisition and normalization | [Live](../windows_observer_agent/live.py), [transport](../windows_observer_agent/transports.py), [adapter](../windows_observer_agent/mcp_adapter.py), [control gate](../windows_observer_agent/control_gate.py) |
| Scene, projection and report | [Scene](../windows_observer_agent/scene_model.py), [planning input](../windows_observer_agent/planning_input.py), [Reporter](../windows_observer_agent/reporter.py), [Replay](../windows_observer_agent/replay.py) |
| External request and validation | [providers](../windows_observer_agent/providers.py), [SDK adapters](../windows_observer_agent/provider_adapters.py), [legacy/Mock provider](../windows_observer_agent/llm_provider.py), [plan parser](../windows_observer_agent/llm_planner.py) |
