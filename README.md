# ParcelPilot Support Agent

A customer-scoped support chatbot for the ParcelPilot take-home assessment. It applies deterministic policy rules from a versioned seed database and requires an explicit Confirm click before its mocked escalation action executes.

The original assessment brief, plan, PDFs, and workbook are intentionally excluded from this repository. `seed/parcelpilot.db` is the deployable runtime snapshot generated from that pack; it is copied into the writable runtime data directory on first startup.

## What is included

- Signed mock customer sessions and deny-by-default account scoping.
- Four server-side agent tools: document search, scoped record lookup, deterministic case evaluation, and escalation proposal.
- Source-priority handling for current policies, account agreements, deprecated policy v2, historical context, and known issues.
- A browser UI showing evidence, override labels, verification states, and protected confirmation controls.
- A deterministic 12-case release gate plus regression tests for privacy, prompt injection, ID enumeration, and action safety.

## Run locally (Windows PowerShell)

Prerequisites: Python 3.12 and, for live chat, either a Groq or OpenAI API key. The UI and all deterministic tests work without a key; `/api/chat` intentionally returns `503` until a provider key is configured.

```powershell
cd C:\parcel-copilot
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
python -m pip install -r requirements-dev.txt
```

If PowerShell prevents activation, run the remaining commands with `.\.venv\Scripts\python.exe` instead.

Set a local session secret. Groq is the recommended local option; it uses the existing OpenAI-compatible client and supports the server-controlled local function tools:

```powershell
$env:SESSION_SECRET = "replace-with-a-long-random-local-secret"
$env:LLM_PROVIDER = "groq"
$env:GROQ_API_KEY = "YOUR_GROQ_API_KEY"
$env:GROQ_MODEL = "openai/gpt-oss-20b" # optional; this is the default
python -m uvicorn parcelpilot.main:app --reload --host 127.0.0.1 --port 8000
```

Alternatively, set `LLM_PROVIDER=openai`, `OPENAI_API_KEY`, and optionally `OPENAI_MODEL`. Never use a placeholder string as an API key; omit the variable to run the non-LLM UI/API paths.

Open `http://127.0.0.1:8000`, choose a demo identity, and use the chat. Check `http://127.0.0.1:8000/health` for `{"status":"ok"}`.

## Verify locally

Run the complete regression suite:

```powershell
python -m unittest discover -s tests -v
```

Run the deterministic assessment release gate:

```powershell
python -m parcelpilot.evals seed\parcelpilot.db
```

Expected result: 12/12 passed, with 100% privacy and action-safety pass rates. These evals intentionally do not call OpenAI.

### Run with Docker Compose (recommended locally)

Copy the environment template, add a newly generated provider key, then start the service:

```powershell
Copy-Item .env.example .env
# Edit .env in your editor and set SESSION_SECRET and GROQ_API_KEY.
docker compose up --build
```

Open `http://localhost:10000`. Stop it with `Ctrl+C`, or from another terminal:

```powershell
docker compose down
```

Compose retains the local SQLite action/audit database in the named `parcelpilot_data` volume. To reset the local demo data completely, run `docker compose down --volumes` (this deletes local action/audit history).

## Solution architecture

```text
Browser UI → signed session + CSRF → FastAPI API → bounded agent loop
                                                  ├─ scoped document/record tools
                                                  ├─ deterministic policy engine
                                                  └─ escalation proposal/confirmation service
                                                               │
                                                        SQLite data snapshot
```

The browser provides the conversation experience, including tool progress, evidence trails, and confirmation controls. FastAPI owns authentication, validation, account scope, and state-changing endpoints. The LLM can choose only from a small set of typed server tools; it cannot query the database directly, supply an account ID, or execute an action.

The SQLite snapshot contains the supplied support documents, typed policy rules, accounts, orders, tickets, action proposals, and audit records. Document search uses SQLite full-text search. A separate authority registry determines which sources are current and eligible before retrieval results are used.

## Key product and technical decisions

### Trustworthy support answers

- **Source authority is explicit.** Active customer agreements override a default policy only where they explicitly cover the topic. Current SOPs and policies are used by default; deprecated policy and historical ticket resolutions remain available for audit/context but cannot become policy authority.
- **Business decisions are deterministic.** Cancellation fees, service credits, severity, and SLA calculations are handled by code in the policy engine rather than left to LLM reasoning.
- **Evidence is visible.** Each answer can show applied, overridden, current, or context-only sources so a customer or reviewer can understand why the system reached its conclusion.
- **Uncertainty is honest.** Missing or conflicting authoritative evidence produces a verification/escalation path instead of a confident unsupported claim.

### Privacy and safe actions

- **Authorization is enforced in the data layer.** The server creates a signed account context and injects it into scoped repositories. Browser input and model tool calls cannot alter that account scope.
- **Unauthorized lookups fail closed.** Unknown and cross-account records return the same generic response, preventing data leakage and record enumeration.
- **Actions require explicit confirmation.** The agent can prepare an escalation proposal only. A dedicated confirmation request validates CSRF, session/account ownership, expiry, immutable payload hash, and idempotency before the mocked action is recorded.

### Practical implementation choices

- **SQLite and lexical full-text search** keep the project portable, transparent, and easy to run locally for the supplied corpus.
- **A bounded tool loop** limits model autonomy and makes tool traces inspectable.
- **Groq or OpenAI support** uses the same OpenAI-compatible client abstraction, while all tools continue to run only on ParcelPilot's server.
- **Automated tests and deterministic evals** cover access control, source precedence, calculations, action safety, prompt-injection resistance, and reliability behavior.

For a production evolution, the first priorities would be OIDC/SAML login, managed Postgres for durable audit/action data, policy approval/versioning, observability, and verified carrier or ticketing integrations.

## Safety and data boundaries

- The browser and model never supply an authoritative account ID or role.
- Account-bound database queries include the session’s account ID; cross-account denials are deliberately generic.
- The agent can create an escalation proposal only. A separate CSRF-protected confirmation endpoint validates session, account, expiry, immutable payload hash, and idempotency before executing the mock action.
- Current, applicable documents are selected before retrieval ranking. Deprecated policy and historical resolutions cannot become policy authority.
- The answerability guard replaces unsupported policy/entitlement claims with a verification request.

See [Architecture](ARCHITECTURE.md), [Product note](PRODUCT.md), and [AI usage](AI_USAGE.md) for design rationale and scope.
