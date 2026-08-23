# Architecture

ParcelPilot is a single FastAPI application with a small static frontend and SQLite storage. The goal is an inspectable customer-support agent, not a general autonomous system.

## Request flow

```text
Browser → signed session + CSRF → FastAPI → scoped tools/policy engine → SQLite
                                      ↘ bounded OpenAI Responses tool loop
```

1. A demo identity is converted server-side into a signed `AuthContext` containing one customer account.
2. The direct Responses API loop accepts only four typed functions. It uses OpenAI directly or Groq through its OpenAI-compatible endpoint, and injects the server-side auth context into the tool service; no model argument can alter scope.
3. Search and record repositories enforce account filtering. The policy engine applies the source-priority registry and returns typed calculations, source traces, and uncertainty.
4. The answerability guard requires authoritative evidence for policy/entitlement claims and turns conflicts or missing facts into visible verification states.
5. Escalations follow proposal → explicit Confirm → transactional mocked execution. Confirmation validates CSRF, account, user, session, expiry, immutable payload hash, and idempotency.

## Authority model

- Active account agreements override defaults only where they explicitly say so.
- The current cancellation/SOP governs cancellation and credits by default.
- Support Policy v3 governs default severity and SLA targets.
- The Product Operations Guide governs capability and known-issue facts.
- Workbook records are authoritative only for snapshot facts; historical ticket resolutions are context-only.
- Support Policy v2 is retained for audit/testing but excluded from normal current retrieval.

The dataset clock is fixed at `2026-08-16 11:00 Asia/Kolkata`. Business-hours calculations use the documented weekday 09:00–18:00 convention with no holiday calendar.

## Trade-offs

- SQLite keeps the assignment self-contained. A hosted production build should move action/audit state to managed Postgres when multi-instance scaling is needed.
- Lexical FTS is adequate for the six-document corpus and gives deterministic, inspectable retrieval. Embeddings are deliberately deferred.
- Mock identities demonstrate the authorization boundary but are not production identity assurance. Replace them with OIDC/SAML and explicit role provisioning.
- The live chat requires an OpenAI API key; deterministic tools, tests, and evals remain runnable without one.
