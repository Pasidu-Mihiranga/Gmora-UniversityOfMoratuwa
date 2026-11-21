# 20. Security and guardrails

Consolidates input guardrails, output guardrails, web security, authentication and authorization
hardening, cache security and abuse limits. Builds on `04-security-and-privacy.md`.

## 1. Authorization: decision points (in order, deny by default)

| Step | Where | Fails with |
|---|---|---|
| 1. Authenticated | `platform.http.auth` dependency verifies the access token | 401 |
| 2. Role allowed for the route | route declaration (`SEC-10` layer 1) | 403 |
| 3. Resource belongs to the principal's scope | handler or module checks patient, case, organization ownership | 404 (`CON-11`) |
| 4. Consent and purpose | `AccessPolicy.decide()` in the data-access layer (`SEC-11`) | 403 or 404, fields filtered |
| 5. Action-level rules | module rules (booking only by the patient's tap, referral only with a treating relationship) | 403 or 409 |

`SG-1` **A route without an explicit role list fails application startup.** A test lists every
route and its roles; adding a route means adding it to that table.

`SG-2` Agents and MCP calls pass the same steps with their Principal (`SEC-10` rule 5, `EXT-12`).
Tool handlers re-check IDs in arguments against the principal's scope (`AGT-42`).

## 2. Authentication hardening (adds to `SEC-1` to `SEC-6`)

`SG-3` Uniform login errors ("Email or password is wrong") and uniform timing, so accounts cannot be
discovered. Password minimum 10 characters, rejected if in a common-password list.
`SG-4` Role or account changes revoke the user's refresh families; access tokens expire within 15
minutes; consent is evaluated per request so consent changes apply immediately.
`SG-5` (P1) TOTP two-factor authentication for `system_admin` and `hospital_admin`.
`SG-6` `POST /auth/refresh` (the only cookie-authenticated route) requires `Origin` to match the
allowed origins and a custom header `X-SuwaPath-Request: 1` (CSRF defence in addition to
`SameSite=Strict`).

## 3. Input guardrails (every request with free text)

Pipeline, in order, before any agent or model sees the text:

1. **Transport limits:** JSON bodies at most 256 KB, uploads per `UPL-2`, request timeout 30 s
   (streams excepted).
2. **Schema validation:** Pydantic with strict types, enums, and a maximum length on every string
   field (patient message 2,000 characters; settings text 500; names 100).
3. **Normalization:** Unicode NFC; strip control characters, zero-width characters and bidirectional
   override characters (they can hide instructions); collapse repeated whitespace.
4. **Language detection:** si, en or other (other is answered in the user's chosen language).
5. **Safety screen (T0):** red-flag rules (`SAF-3`, `NAV-1`). Always runs, never cached or skipped.
6. **Injection screen:** pattern rules (role-play prompts, "ignore previous", system-prompt requests,
   tool or JSON syntax, encoded payloads) plus, when available, a Jev noul question "does this text
   try to instruct the system". A positive result **does not block the patient**; it marks the turn
   `suspected_injection`, which disables write tools for that turn (holds and bookings need a fresh
   confirmation), tells the verifier, and is audited.
7. **Quota and rate limits** (section 6).

`SG-7` Text is always wrapped as `UntrustedText` and rendered to models in a data block (`GW-21`).
Text extracted from uploaded documents goes through steps 3 and 6 too.

## 4. Output guardrails (every model output)

`SG-8` Structured outputs must validate against their schema (`GW-1` pipeline step 5); free text is
length-limited and checked for the expected language.
`SG-9` The verifier (`VER-2`, `VER-3`) checks every cross-role message and every patient-facing
clinical text: scope, wording, urgency, sources, canary, injected-instruction following.
`SG-10` **No system-prompt or tool-schema echo:** a deterministic check rejects outputs that contain
long verbatim fragments of the agent's prompt files.
`SG-11` Outputs going to someone other than the patient pass the PHI inspector (`SEC-30` step 4) in
reverse: no identifiers of anyone outside the recipient's access.
`SG-12` **Tool calls are outputs too:** arguments are validated, IDs are re-checked (`AGT-42`), and
`write_irreversible` tools need a person's action. (P1) For write tools, a Jev noul check confirms
the call is supported by what the person actually asked; unsupported calls are refused.

## 5. Frontend security

`SG-13` **Rendering model text:** never as HTML. A Markdown renderer with raw HTML disabled, a small
allowlist of elements (paragraphs, lists, bold, italic, line breaks), **no images**, and links only
to `https:` URLs on an allowlist (our domain, the cited corpus sources). Other links are shown as
plain text. This blocks XSS and data exfiltration through injected image or link URLs.
`SG-14` `dangerouslySetInnerHTML`, `eval`, `new Function` and `innerHTML` are banned by lint rules.
React's escaping is relied on for all other text.
`SG-15` Client-side validation is for usability only. The backend is authoritative (`CUS-15`).
`SG-16` On logout or user switch, the TanStack Query cache, in-memory tokens and any private consult
state are cleared. Nothing with health data is put in `localStorage`, `sessionStorage` or a service
worker (`FE-33`, `PRV-13`).

## 6. HTTP security

`SG-17` Headers on every response: `Strict-Transport-Security` (production), `X-Content-Type-Options:
nosniff`, `Referrer-Policy: no-referrer`, `X-Frame-Options: DENY`, `Permissions-Policy` (camera only
on capture pages, no geolocation unless the user taps "use my location"),
`Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline';
img-src 'self' data: blob:; connect-src 'self' <api-origin>; frame-ancestors 'none';
base-uri 'none'; form-action 'self'`.
`SG-18` CORS allows only the configured web origins, with credentials only for the refresh route.
`SG-19` **Every API response is `Cache-Control: no-store`** (except health checks and static assets),
so no browser, proxy or CDN stores health data and web-cache deception cannot work.
`SG-20` Rate limits (token bucket per user and per IP, per process; quotas in `ops_usage` are the
cross-process limit for model use):

| Route class | Limit |
|---|---|
| login, refresh | 10 per minute per IP, plus lockout (`SEC-1`) |
| chat and private consult turns | 20 per minute per user |
| search | 60 per minute per user |
| uploads | 10 per minute per user |
| everything else | 120 per minute per user |
| SSE streams | 5 open per user |

`SG-21` The server never fetches URLs supplied by users or models (no SSRF surface). Outbound HTTP
goes only to configured provider hosts.

## 7. Cache and retrieval security

`SG-22` **Shared answer cache (poisoning defence).** The shared cache is keyed by a **canonical
topic** from the knowledge base (for example `lab_test:hba1c:what_is`, `si`), not by the user's
text. A question is mapped to a topic by retrieval plus the classifier; if no topic matches with
high confidence, the shared cache is not used. The cached answer is generated from the **canonical
topic question and corpus chunks only**, never from the user's own wording, so a crafted question
cannot plant content for others. Entries also need the verifier's `allow` and a faithfulness check
against their cited chunks. Admins can purge an entry; every write is audited. (Replaces the
normalized-question key of `KNW-30`.)
`SG-23` **User cache:** per patient, encrypted, fingerprint-checked and access-checked on every hit
(`KNW-33` to `KNW-35`); a poisoned entry could only affect its own owner.
`SG-24` **Corpus poisoning:** only files in the repository are ingested, by CLI, after review;
content hashes are recorded and shown on the AI operations page. No user content ever enters the
corpus or its embeddings.
`SG-25` **Server caches** (DEK cache, flags, config) are in-process, keyed by IDs, never by user
input, and hold no response bodies.

## 8. Injection classes outside the model

`SG-26` SQL: SQLAlchemy with bound parameters only; raw SQL strings are banned except in migrations
(ruff `S608`). No `pickle` or unsafe YAML (ruff `S301`, `S506`); the LangGraph checkpointer uses its
JSON serializer. No shell commands from request paths. File storage uses generated IDs, never user
file names (no path traversal).

## 9. Supply chain

`SG-27` Dependencies are pinned (`requirements.txt`, `package-lock.json`). CI runs `pip-audit` and
`npm audit --audit-level=high`; a high finding fails the build unless an exception is recorded in
the pull request.

## 10. OWASP Top 10 for LLM Applications (2025) mapping

| Risk | Controls |
|---|---|
| LLM01 Prompt injection | untrusted data blocks, injection screen, verifier `follows_injected_instruction`, tools authorize independently, write tools need confirmation |
| LLM02 Sensitive information disclosure | consent at the data layer, PHI minimization and inspector, canary check, agent context isolation, private consult design |
| LLM03 Supply chain | pinned dependencies, audits, model weights with SHA-256 manifests |
| LLM04 Data and model poisoning | curated corpus with hashes, canonical-topic shared cache, no user content in corpus |
| LLM05 Improper output handling | safe Markdown renderer, no HTML, link allowlist, schema validation |
| LLM06 Excessive agency | tool allowlists, risk tiers, person-only irreversible actions, agent principal is an intersection |
| LLM07 System prompt leakage | prompts hold no secrets; echo check `SG-10` |
| LLM08 Vector and embedding weaknesses | no PHI embedded, retrieval only over the public corpus |
| LLM09 Misinformation | red-flag floor in code, grounded answers with citations, verifier `unsupported_claim`, doctor approval |
| LLM10 Unbounded consumption | budgets, per-person quotas, rate limits, step and time caps |

## 11. Tests

Security tests in CI (`QA-5`) also cover: route role table completeness (`SG-1`), security headers
and `no-store` on API responses, CSRF checks on refresh, input normalization (zero-width and bidi
characters removed), Markdown renderer rejecting HTML, images and disallowed links, shared cache
refusing non-canonical questions, and rate limits. The `privacy` evaluation suite includes attacks
through report uploads, cache poisoning attempts and output-rendering payloads.
