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

ReplayClient reads a local woa.replay.v1 JSON file, requires redacted flags, sanitizes records and builds a historical observation. Flags and filenames are not proof that the original file is safe: the whole file is read before sanitization, and it is not rewritten or erased.

Replay does not observe today's desktop or grant current control. With rule or Mock LLM it is offline. Selecting an authorized external provider sends the reduced historical goal/Scene projection to that provider.

### D. Windows-MCP Live Mode

Live requires --live and an explicit --endpoint. StreamableHTTPTransport accepts a literal 127.0.0.1 host, validates its URL, and reads WOA_MCP_TOKEN (at least 32 ASCII characters without whitespace). It does not scan for a server, start one, or fall back to stdio.

After initialize and tool discovery, collection is sequential: ControlStatus, Screenshot with use_annotation=false, then Snapshot with use_vision=false, use_dom=false and use_ui_tree=true. Only these three tools are allowed. Control must parse successfully as ready or agent (the internal mapping of ai); user takeover, unavailable/unknown state, refusal or failure stops collection.

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

## Data Redaction and Minimization

Raw MCP normalization removes recognized image blocks, but preserves text and may preserve unknown fields. Scene construction is not global anonymization.

Live CLI uses public_live_observation and sanitize_record: titles/handles become aliases, control names are replaced, unknown lines are discarded, while recognized types, coordinates, dimensions, status, timestamps and capture-limit information can remain.

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

Model API keys authenticate the selected model service. WOA_MCP_TOKEN authenticates the local MCP session; these are different secrets and destinations. Named model keys are read from the configured environment variable; the legacy model key is WOA_LLM_API_KEY.

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
