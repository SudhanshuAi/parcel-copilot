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

## Run with Docker

The Docker image includes only the application and the seed database. It creates its writable SQLite copy on first startup if it is absent.

```powershell
docker build -t parcelpilot-support .
docker run --rm -p 10000:10000 `
  -e PARCELPILOT_ENV=production `
  -e PARCELPILOT_SECURE_COOKIES=false `
  -e SESSION_SECRET="replace-with-a-long-random-secret" `
  -e LLM_PROVIDER="groq" `
  -e GROQ_API_KEY="YOUR_GROQ_API_KEY" `
  -e GROQ_MODEL="openai/gpt-oss-20b" `
  parcelpilot-support
```

`PARCELPILOT_SECURE_COOKIES=false` is only for local HTTP Docker testing. Leave it unset in a hosted HTTPS environment.

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

## Deploy to Render (do after local verification)

1. Push this project to a private or public Git repository. Do not commit `.env`, API keys, or `data/*.db`.
2. In Render, create **New → Web Service**, connect the repository, and select the **Docker** runtime. Render runs the image’s `CMD`, which already binds Uvicorn to Render’s `PORT` value.
3. Set the health-check path to `/health`.
4. In the service’s environment settings, add:

   | Key | Value |
   | --- | --- |
   | `PARCELPILOT_ENV` | `production` |
   | `SESSION_SECRET` | A new long random secret; never reuse the local value. |
   | `LLM_PROVIDER` | `groq` (or `openai` if using OpenAI instead). |
   | `GROQ_API_KEY` | Your Groq API key, saved as a secret. |
   | `GROQ_MODEL` | Optional; defaults to `openai/gpt-oss-20b`. |

5. For persistent escalation/audit state, attach a persistent disk mounted at `/var/data` and set `PARCELPILOT_DATABASE_PATH=/var/data/parcelpilot.db`. Persistent disks require a paid Render service. Without a disk, the app remains usable but SQLite-backed proposals/audit records reset after a restart or deploy.
6. Deploy, wait for Render’s health check to pass, then open the assigned `onrender.com` URL. Confirm the login page, `/health`, one scoped lookup, and one proposal/Confirm cycle manually.

Render web services accept a repository or Docker image, use the `PORT` environment variable for public HTTP traffic, and expose environment settings, health checks, and persistent disks in the service configuration. See the official [Render web-service guide](https://render.com/docs/web-services), [Docker guide](https://render.com/docs/docker), and [persistent-disk guide](https://render.com/docs/disks).

Groq is configured through its OpenAI-compatible endpoint (`https://api.groq.com/openai/v1`). The default Groq model supports local function calling, so ParcelPilot continues to execute tools only on its own server. See Groq’s [OpenAI compatibility](https://console.groq.com/docs/openai) and [local tool-calling](https://console.groq.com/docs/tool-use/local-tool-calling) documentation.

## Safety and data boundaries

- The browser and model never supply an authoritative account ID or role.
- Account-bound database queries include the session’s account ID; cross-account denials are deliberately generic.
- The agent can create an escalation proposal only. A separate CSRF-protected confirmation endpoint validates session, account, expiry, immutable payload hash, and idempotency before executing the mock action.
- Current, applicable documents are selected before retrieval ranking. Deprecated policy and historical resolutions cannot become policy authority.
- The answerability guard replaces unsupported policy/entitlement claims with a verification request.

See [Architecture](ARCHITECTURE.md), [Product note](PRODUCT.md), and [AI usage](AI_USAGE.md) for design rationale and scope.
