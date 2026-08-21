import secrets

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.integrations.discord.discord_auth_service import (
    DiscordIntegrationError,
    build_authorization_url,
    exchange_code_for_tokens,
    get_current_user,
    save_or_update_connection,
    verify_bot_guild_access,
)


STATE_COOKIE_NAME = "discord_oauth_state"
STATE_COOKIE_MAX_AGE = 600

router = APIRouter(prefix="/integrations/discord", tags=["discord"])


def _state_cookie_is_secure() -> bool:
    return bool(settings.discord_redirect_uri and settings.discord_redirect_uri.startswith("https://"))


def _error_response(
    status_code: int, detail: str, *, clear_state: bool = False
) -> JSONResponse:
    response = JSONResponse(status_code=status_code, content={"detail": detail})
    if clear_state:
        response.delete_cookie(
            STATE_COOKIE_NAME, secure=_state_cookie_is_secure(), samesite="lax"
        )
    return response


@router.get("/authorize")
def authorize() -> Response:
    state = secrets.token_urlsafe(32)
    try:
        authorization_url = build_authorization_url(state)
    except DiscordIntegrationError as exc:
        return _error_response(exc.status_code, str(exc))

    response = RedirectResponse(url=authorization_url, status_code=302)
    response.set_cookie(
        key=STATE_COOKIE_NAME,
        value=state,
        max_age=STATE_COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=_state_cookie_is_secure(),
    )
    return response


@router.get("/callback")
def callback(
    request: Request,
    session: Session = Depends(get_db),
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    guild_id: str | None = Query(default=None),
    permissions: str | None = Query(default=None),
) -> JSONResponse:
    stored_state = request.cookies.get(STATE_COOKIE_NAME)
    if not stored_state or not state:
        return _error_response(400, "Discord OAuth state is missing.")
    if not secrets.compare_digest(stored_state, state):
        return _error_response(400, "Discord OAuth state is invalid.")
    if error:
        return _error_response(400, "Discord authorization was denied.", clear_state=True)
    if not code:
        return _error_response(400, "Discord authorization code is missing.", clear_state=True)
    if permissions is not None and not permissions.isdigit():
        return _error_response(400, "Discord permissions are invalid.", clear_state=True)

    try:
        token_data = exchange_code_for_tokens(code)
        token_guild_id = token_data.get("guild_id")
        if not isinstance(token_guild_id, str) or not token_guild_id:
            return _error_response(400, "Discord guild is missing.", clear_state=True)
        if guild_id and token_guild_id != guild_id:
            return _error_response(
                400, "Discord guild information is inconsistent.", clear_state=True
            )

        discord_user_id = get_current_user(token_data["access_token"])
        verify_bot_guild_access(token_guild_id)
        connection = save_or_update_connection(
            session,
            discord_user_id=discord_user_id,
            guild_id=token_guild_id,
            token_data=token_data,
            granted_permissions=permissions,
        )
    except DiscordIntegrationError as exc:
        return _error_response(exc.status_code, str(exc), clear_state=True)

    response = JSONResponse(
        status_code=200,
        content={
            "connected": True,
            "discord_user_id": connection.discord_user_id,
            "guild_id": connection.guild_id,
        },
    )
    response.delete_cookie(STATE_COOKIE_NAME, secure=_state_cookie_is_secure(), samesite="lax")
    return response
