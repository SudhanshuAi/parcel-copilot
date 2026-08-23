"""Minimal M2 API surface that proves authentication and account scoping."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from parcelpilot.auth import AuthenticationError, AuthContext, DEMO_IDENTITIES, SessionSigner
from parcelpilot.agent import AgentLoop, ModelBackendError, configured_agent
from parcelpilot.actions import ActionError, ActionService
from parcelpilot.repositories import NotFoundOrNotAuthorized, ScopedRepository
from parcelpilot.tools import ToolService


SESSION_COOKIE = "parcelpilot_session"
CSRF_COOKIE = "parcelpilot_csrf"
DEVELOPMENT_SESSION_SECRET = "development-only-session-secret-change-me-32"


class DemoLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    identity: str = Field(pattern="^(northstar_demo|lumenworks_demo|beacon_demo|axis_demo)$")


class EvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_type: Literal["cancellation", "failed_pickup_credit", "severity", "first_response_sla"]
    record_type: Literal["order", "ticket"]
    record_id: str = Field(min_length=1, max_length=64)
    reported_facts: dict[str, str] | None = None


class ChatHistoryItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=2_000)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=4_000)
    history: list[ChatHistoryItem] = Field(default_factory=list, max_length=10)


class ProposalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_type: Literal["ticket", "order", "general_request"]
    target_id: str | None = Field(default=None, max_length=64)
    reason_code: Literal["P1", "SLA_BREACH", "POLICY_EXCEPTION", "DATA_CONFLICT", "OUT_OF_SCOPE", "OTHER"]
    summary: str = Field(min_length=1, max_length=500)
    evidence_source_ids: list[str] = Field(default_factory=list, max_length=12)


class ConfirmationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_payload_hash: str = Field(min_length=64, max_length=64, pattern="^[0-9a-f]{64}$")


def _auth_context(request: Request, session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE)) -> AuthContext:
    if not session_token:
        raise HTTPException(status_code=401, detail="Authentication required.")
    try:
        return request.app.state.session_signer.verify(session_token)
    except AuthenticationError as exc:
        raise HTTPException(status_code=401, detail="Authentication required.") from exc


def _repository(request: Request, auth: AuthContext = Depends(_auth_context)) -> ScopedRepository:
    return ScopedRepository(request.app.state.database_path, auth)


def _tool_service(request: Request, auth: AuthContext = Depends(_auth_context)) -> ToolService:
    return ToolService(request.app.state.database_path, auth)


def _action_service(request: Request, auth: AuthContext = Depends(_auth_context)) -> ActionService:
    return ActionService(request.app.state.database_path, auth)


def require_csrf(
    request: Request,
    auth: AuthContext = Depends(_auth_context),
    csrf_cookie: str | None = Cookie(default=None, alias=CSRF_COOKIE),
    csrf_header: str | None = Header(default=None, alias="X-CSRF-Token"),
) -> AuthContext:
    if not csrf_cookie or not csrf_header or csrf_cookie != csrf_header:
        raise HTTPException(status_code=403, detail="Invalid CSRF token.")
    if not request.app.state.session_signer.verify_csrf(csrf_header, auth):
        raise HTTPException(status_code=403, detail="Invalid CSRF token.")
    return auth


def create_app(
    database_path: Path,
    *,
    session_secret: str = DEVELOPMENT_SESSION_SECRET,
    secure_cookies: bool = False,
    agent_loop: AgentLoop | None = None,
) -> FastAPI:
    app = FastAPI(title="ParcelPilot Support Agent", version="0.1.0")
    app.state.database_path = database_path
    app.state.session_signer = SessionSigner(session_secret)
    app.state.secure_cookies = secure_cookies
    app.state.agent_loop = agent_loop if agent_loop is not None else configured_agent(database_path)
    static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    @app.get("/health", include_in_schema=False)
    def health() -> dict[str, str]:
        """Unauthenticated liveness endpoint for local and hosting checks."""
        return {"status": "ok"}

    @app.post("/auth/demo-login")
    def demo_login(payload: DemoLoginRequest, response: Response) -> dict[str, bool]:
        token = app.state.session_signer.issue(payload.identity)
        auth = app.state.session_signer.verify(token)
        response.set_cookie(
            key=SESSION_COOKIE,
            value=token,
            httponly=True,
            secure=app.state.secure_cookies,
            samesite="lax",
            max_age=8 * 60 * 60,
            path="/",
        )
        response.set_cookie(
            key=CSRF_COOKIE,
            value=app.state.session_signer.issue_csrf(auth.session_id),
            httponly=False,
            secure=app.state.secure_cookies,
            samesite="lax",
            max_age=8 * 60 * 60,
            path="/",
        )
        return {"authenticated": True}

    @app.post("/auth/logout")
    def logout(response: Response, _: AuthContext = Depends(require_csrf)) -> dict[str, bool]:
        response.delete_cookie(SESSION_COOKIE, httponly=True, samesite="lax", path="/")
        response.delete_cookie(CSRF_COOKIE, httponly=False, samesite="lax", path="/")
        return {"authenticated": False}

    @app.get("/api/demo-identities")
    def demo_identities() -> list[dict[str, str]]:
        return [
            {"identity": identity.identity_id, "display_name": identity.display_name}
            for identity in DEMO_IDENTITIES.values()
        ]

    @app.get("/api/me")
    def me(auth: AuthContext = Depends(_auth_context), repository: ScopedRepository = Depends(_repository)) -> dict[str, object]:
        return {"role": auth.role, "account": repository.get_account()}

    @app.get("/api/orders/{order_id}")
    def order(order_id: str, repository: ScopedRepository = Depends(_repository)) -> dict[str, object]:
        try:
            return {"record": repository.get_order(order_id)}
        except NotFoundOrNotAuthorized as exc:
            raise HTTPException(status_code=404, detail="Record not found.") from exc

    @app.get("/api/tickets/{ticket_id}")
    def ticket(ticket_id: str, repository: ScopedRepository = Depends(_repository)) -> dict[str, object]:
        try:
            return {"record": repository.get_ticket(ticket_id)}
        except NotFoundOrNotAuthorized as exc:
            raise HTTPException(status_code=404, detail="Record not found.") from exc

    @app.get("/api/documents/search")
    def document_search(query: str, topic: str, limit: int = 5, repository: ScopedRepository = Depends(_repository)) -> dict[str, object]:
        try:
            return {"results": repository.search_documents(query=query, topic=topic, limit=limit)}
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Invalid document search request.") from exc

    @app.post("/api/evaluate")
    def evaluate(payload: EvaluationRequest, tools: ToolService = Depends(_tool_service)) -> dict[str, object]:
        try:
            result = tools.evaluate_case(
                case_type=payload.case_type,
                record_type=payload.record_type,
                record_id=payload.record_id,
                reported_facts=payload.reported_facts,
            )
            return result.as_dict()
        except NotFoundOrNotAuthorized as exc:
            raise HTTPException(status_code=404, detail="Record not found.") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Invalid evaluation request.") from exc

    @app.post("/api/chat")
    def chat(payload: ChatRequest, request: Request, auth: AuthContext = Depends(_auth_context)) -> dict[str, Any]:
        agent: AgentLoop | None = request.app.state.agent_loop
        if agent is None:
            raise HTTPException(status_code=503, detail="Chat model is not configured.")
        try:
            result = agent.run(
                auth=auth,
                user_message=payload.message,
                history=[item.model_dump() for item in payload.history],
            )
            return result.as_dict()
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Invalid chat request.") from exc
        except ModelBackendError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/api/actions/proposals")
    def propose_action(
        payload: ProposalRequest,
        _: AuthContext = Depends(require_csrf),
        actions: ActionService = Depends(_action_service),
    ) -> dict[str, object]:
        try:
            return {"proposal": actions.propose_escalation(**payload.model_dump())}
        except NotFoundOrNotAuthorized as exc:
            raise HTTPException(status_code=404, detail="Proposal or target not found.") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Invalid escalation proposal.") from exc

    @app.post("/api/actions/{proposal_id}/confirm")
    def confirm_action(
        proposal_id: str,
        payload: ConfirmationRequest,
        _: AuthContext = Depends(require_csrf),
        actions: ActionService = Depends(_action_service),
    ) -> dict[str, object]:
        try:
            return {"action": actions.confirm_and_execute(proposal_id, payload.expected_payload_hash).as_dict()}
        except NotFoundOrNotAuthorized as exc:
            raise HTTPException(status_code=404, detail="Proposal not found.") from exc
        except ActionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/actions/{proposal_id}/cancel")
    def cancel_action(
        proposal_id: str,
        _: AuthContext = Depends(require_csrf),
        actions: ActionService = Depends(_action_service),
    ) -> dict[str, object]:
        try:
            return {"proposal": actions.cancel_proposal(proposal_id)}
        except NotFoundOrNotAuthorized as exc:
            raise HTTPException(status_code=404, detail="Proposal not found.") from exc
        except ActionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    return app
