# M0 Acceptance Contract

This document locks the implementation choices and non-negotiable checks for the ParcelPilot assessment. Changes require an explicit revision before the affected milestone begins.

## Approved implementation defaults

| Decision | Locked choice |
|---|---|
| User context | Customer-facing support chatbot with one server-issued, account-scoped session per demo user. |
| Additional problem | Problem 2: Trust and Reliability. |
| Backend/UI | Python 3.12, FastAPI, Pydantic, server-rendered Jinja/HTMX. |
| Data | SQLite source/action store; FTS5 retrieval; document embeddings are optional enhancement, never required for deterministic tests. |
| Agent | Direct OpenAI Responses API function-calling loop. No LangChain, LlamaIndex agent orchestration, or agent framework. The model/provider ID remains environment-configurable. |
| Hosting target | One Dockerized FastAPI service on Render after the local build passes. |
| Authentication demo | Identity selector -> server maps identity to immutable account/role -> signed HttpOnly cookie. `account_id` and role are never accepted from the model or normal chat client payload. |
| Dataset clock | `2026-08-16T11:00:00+05:30` (Asia/Kolkata), read from the workbook README during ingestion. |
| Business calendar assumption | Monday-Friday, 09:00-18:00 Asia/Kolkata; no holiday calendar. Calculations must label this assumption. |
| State-changing capability | Mocked escalation only. It must use proposal -> explicit authenticated Confirm control -> idempotent execution. Free-text approval is insufficient. |

## Scope boundaries

- The supplied data pack is the sole information base for user-facing answers.
- The app may use an LLM to choose tools and compose grounded answers, but entitlement, arithmetic, source precedence, authorization, and execution decisions live in server code.
- Customer sessions may access global current policy/product material and only their own account, orders, tickets, and active agreement.
- Actual cancellation, refund/credit issuance, fee waivers, carrier calls, production SSO, and production ticket-system integration are out of scope. These requests escalate rather than execute.
- Historical ticket resolutions are context only. They cannot establish current policy, product limits, entitlements, or action authority.

## Frozen source manifest

M1 ingestion must fail rather than silently process a changed source pack unless the manifest is deliberately updated.

| File | SHA-256 | Required handling |
|---|---|---|
| `01_Support_Policy_v3_CURRENT.pdf` | `DB7097BB1E881327954282B9A4FBCE8CBBC08D6868AB000478935BC00EE11FA2` | Current default support severity/SLA authority. |
| `02_Support_Policy_v2_DEPRECATED.pdf` | `14C3B549474D8079D600935E915483F5085FCC5EDB685C9A9B5CDCC4E687AA27` | Audit-only; excluded from normal current-answer retrieval. |
| `03_Cancellation_and_Service_Credit_SOP_v4.pdf` | `79C20A835F868888236B3166FFB185E5A7921F48BEB309AC79266489E121B006` | Current default cancellation and service-credit authority. |
| `04_Product_Operations_Guide_and_Known_Issues.pdf` | `983BA3A75CEFD62FA39C600E2EF56FB4860B19557D4E42E356C631B45A2249D4` | Current product capability/known-issue authority. |
| `05_Northstar_Logistics_Enterprise_Agreement.pdf` | `59E413076B34F5821BF7E3A9834D58CE0ED18883A40D07560D97A998406701CB` | Active agreement scoped only to ACCT-001. |
| `06_LumenWorks_Service_Agreement.pdf` | `294EAE8CE53BD2FAB97A9A3086C56BE98FEC142A512E9EA8ACF24D88DF70B2A8` | Active agreement scoped only to ACCT-002. |
| `ParcelPilot_Assessment_Data.xlsx` | `4E69DBFD08B79FDA6266BD7D8CACE2CC3420565A46FAD9D6A4DE3DB9A11E0A72` | Snapshot facts and fixed reference time. |

## Acceptance criteria

| Assessment requirement | Required implementation evidence | Release gate |
|---|---|---|
| Natural-language chatbot | A chat endpoint invokes a direct tool-calling loop and returns a grounded response, citations, uncertainty, and visible tool trace. | The two assessment examples work without hard-coded answer text. |
| Source reliability | A deterministic authority registry applies active account terms only to the relevant topic/account; v2 is never current authority; historical resolutions are context-only. | All source-conflict tests pass. |
| Data privacy | Scoped data/document repositories accept server `AuthContext` only and use account predicates before lookup/ranking. | Cross-account endpoint and repository tests disclose nothing, including existence. |
| Three distinct tools | Document retrieval, structured lookup, deterministic case evaluation, proposal, and protected execution are separately typed tools. | Multi-step tests exercise at least document + structured + evaluation; action tests use proposal/execution. |
| Confirmation before action | Pending proposal binds account/session/payload/expiry; only a protected confirmation endpoint calls executor. | Free text, replay, stale, altered, and cross-account confirmation attempts cannot create a second/unauthorized action. |
| Escalation/uncertainty | Missing required facts, unresolved authoritative conflict, unsupported exception, or out-of-scope action produces verification/escalation rather than a promise. | Indeterminate-case tests pass. |
| Interface/tool visibility | Chat page has mock identity indicator, tool trace, citations, and Confirm/Cancel action card. | Manual browser smoke test passes. |
| Trust problem | Evidence chips/decision trace expose winning and overridden sources without hidden chain-of-thought. | Deprecated/context-only traps are surfaced correctly. |

## Security and reliability threat cases

| Threat | Required control | Test shape |
|---|---|---|
| Model supplies another `account_id` | `AuthContext` is injected and model schemas omit authoritative account/role fields. | Call every repository/tool with hostile arguments; scope remains session account. |
| Guessing another account's order/ticket | Query binds ID plus scoped account and returns generic result. | ACCT-002 asks for ORD-1001/TKT-501; no value, title, customer name, or existence confirmation leaks. |
| Private agreement retrieval | Document pre-filter permits only global current documents plus matching active agreement. | ACCT-002 search cannot retrieve Northstar clause/text/metadata. |
| Deprecated-policy instruction | Deprecated chunks are excluded from current retrieval and evaluator ignores them. | Prompt asks to use v2; result uses v3. |
| Incorrect historical resolution | Context-only text is excluded from normative evaluation. | TKT-451 cannot turn the 3,000-row workaround into a plan limit. |
| Prompt injection in user/document text | Evidence is treated as data; fixed server tool registry and schemas remain authoritative. | Hostile text cannot enable arbitrary SQL/file access or bypass confirmation. |
| Unconfirmed action | Proposal cannot mutate ticket/escalation state. | “Escalate now” yields proposal only; “yes” in chat does not execute. |
| Replay/race | Atomic state transition and unique proposal/action key. | Two confirmation requests create one action only. |
| Stale/conflicting facts | Evaluation returns `needs_verification`; executor rechecks proposal/state. | SwiftShip lag and missing monthly credit-cap usage fail closed. |

## Deterministic acceptance scenarios

The full language-level eval set remains in `PLAN.md`. These lower-level assertions must be present before the live-model eval is trusted:

1. Northstar `ORD-1001`: BOOKED/pre-pickup cancellation is no-fee because its agreement overrides the SOP's INR 250 after-30-minute default.
2. Northstar `ORD-1002`: PICKED_UP cannot be cancelled; return-to-origin is the applicable process.
3. LumenWorks `ORD-2001`: 75 minutes after booking, BOOKED/pre-pickup, INR 250 fee applies.
4. LumenWorks `ORD-2002`: at the snapshot it is 4.5 hours past pickup-window end with carrier fault and no customer fault; custom rule yields INR 300 subject to any required verification.
5. Beacon `ORD-3001`: cancellation within 30 minutes is no-fee under the default SOP.
6. Northstar `TKT-501`: shipment-creation failure is P1; the ACCT-001 agreement replaces the normal target with 15 minutes, 24x7. Report a passed deadline, but do not claim a first response was absent when no such event exists in the data.
7. LumenWorks `TKT-502`: Growth supports up to 5,000 rows; KI-208 explains failures over roughly 3,000 and its workaround. TKT-451 is not authority.
8. Northstar `TKT-504`: SwiftShip status can lag for up to 20 minutes, so a reported pickup after 10 minutes requires wait/verification rather than a missed-pickup conclusion.
9. Standard-plan P2: current Support Policy v3 gives one business day; v2's two-business-day value is rejected as deprecated.
10. A cross-account lookup or agreement search returns `not_found_or_not_authorized` without leakage.
11. A goodwill waiver request produces an escalation proposal, not a waiver.
12. A valid confirmation executes the matching proposal once; free-text, cross-account, expired, altered, and replay attempts do not create an additional action.

## M0 completion conditions

- All choices above are treated as approved by the owner's `approved` instruction.
- The manifest has seven exact source hashes.
- Each assessment requirement has a named implementation evidence and release gate.
- Security/reliability tests are specified before application code exists.
- The next milestone is M1 only; it may now create the application structure, ingestion code, tests, and local derived database as needed.
