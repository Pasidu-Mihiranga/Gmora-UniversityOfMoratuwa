# 15. Extension points, feature flags and MCP

## 1. Registries

`EXT-1` Each extension point is a small `Protocol` in `contracts/` and a registry in
`platform/registry.py`. Modules register implementations in their `module.register()`.
Nothing scans packages or uses plugin frameworks.

| Registry | Interface | Registered by | MVP entries | Add one by |
|---|---|---|---|---|
| `agents` | `Agent` | agents | companion, guardian agent, doctor assistant, baseline | new class + `AgentSpec` + prompts + register |
| `tools` | `ToolSpec` + handler | any module | see `05-agents-and-verifier.md` | spec with Pydantic input and output + handler |
| `subscribers` | `(event_type, handler)` | any module | see event catalog | handler + idempotency test |
| `alert_rules` | `AlertRule` | care | `missed_doses`, `no_checkin_response`, `red_flag` | class with `evaluate(summary) -> Fired or None` |
| `programmes` | `CareProgramme` | care | `elder_care` | class + si/en content |
| `imaging_models` | `ImagingModel` | imaging | pneumonia general, paediatric | manifest + adapter (ONNX adapter is generic) |
| `report_parsers` | `ReportParser` | reports | text layer, vision, OCR | class with `can_parse` and `parse` |
| `verifier_checks` | `VerifierCheck` | verifier | scope, wording, urgency, sources, canary, format | class returning `CheckResult` |
| `notification_channels` | `NotificationChannel` | notifications | in-app, SMS (no-op sender) | class with `send` |
| `model_providers` | `ChatModel`, `DecisionModel` | platform | gemini, openai_compatible, decisions, fake | adapter + capabilities |

## 2. Feature flags

`EXT-2` `platform_flags(key, enabled, description, updated_at, updated_by)`, read through
`flags.is_on(key)` (cached for 10 s). Admins toggle them on `/admin/flags`. New features ship behind
a flag that defaults to off until demoed.

## 3. Last-day feature playbook

When a new requirement arrives:
1. Write the change as a spec delta (which registry, which events, which routes) in a
   `contract-change` pull request. Usually it adds rows, it does not change existing ones.
2. Prefer, in this order: a new registry entry; a new subscriber to existing events; a new module;
   changing an existing module last.
3. Put it behind a feature flag.
4. Add its tests at the risk tier it touches.

Examples: "add a pregnancy programme" = new `CareProgramme` + alert rules + si/en content.
"Notify by WhatsApp" = new `NotificationChannel`. "Add a skin lesion model" = new manifest and
registration. "Add a pharmacy agent" = new `Agent` + tools + intents.

## 4. MCP (P2)

`EXT-10` Built only after issue I02 (Amma end to end) is green.

`EXT-11` `python main.py mcp` runs an MCP server over stdio using the official MCP Python SDK. It is a
thin adapter that exposes tools from the tool registry whose `ToolSpec` is marked `mcp_exposed=True`.

`EXT-12` Every MCP call runs as a `Principal` built from a scoped token given on startup
(`SUWAPATH_MCP_TOKEN`, created by `python main.py mcp-token --user <id> --scopes ...`). The same
access policy, PHI minimization, verifier rules (for anything delivered to another person) and audit
apply. There is no separate MCP code path to data.

`EXT-13` Phase A exposes only tools without patient data: `safety.check_symptoms_urgency`
(input text, returns urgency and 1990 advice), `navigation.route`, `navigation.find_doctors`,
`booking.list_sessions`, `care.list_programmes`. Phase B (only if time allows) adds "my record"
tools for an authenticated patient.

`EXT-14` The MCP server is included in the privacy suite: the same attacks are sent through MCP.

`EXT-15` An MCP client (calling outside MCP servers as tools) is out of the MVP. The tool registry
already allows adding one later.
