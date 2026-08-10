from functools import lru_cache
from uuid import UUID

from fastapi import Depends, Header, HTTPException

from .config import settings


@lru_cache(maxsize=1)
def _jwks_client():
    from jwt import PyJWKClient

    if not settings.supabase_url:
        raise RuntimeError("SUPABASE_URL is required in online mode.")
    url = settings.supabase_url.rstrip("/") + "/auth/v1/.well-known/jwks.json"
    return PyJWKClient(url, cache_jwk_set=True, lifespan=600)


def get_current_user(authorization: str | None = Header(default=None)) -> UUID | None:
    if settings.deployment_mode.strip().lower() != "online":
        return None
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Sign in is required.")
    token = authorization.split(" ", 1)[1].strip()
    try:
        import jwt

        signing_key = _jwks_client().get_signing_key_from_jwt(token)
        issuer = settings.supabase_jwt_issuer or (
            settings.supabase_url.rstrip("/") + "/auth/v1" if settings.supabase_url else None
        )
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["ES256", "RS256"],
            audience=settings.supabase_jwt_audience,
            issuer=issuer,
        )
        if claims.get("role") != "authenticated":
            raise HTTPException(403, "An authenticated user account is required.")
        return UUID(claims["sub"])
    except HTTPException:
        raise
    except (KeyError, ValueError) as error:
        raise HTTPException(401, "Your session is invalid or has expired. Sign in again.") from error
    except Exception as error:
        # PyJWT is loaded only in online mode so the current local installation
        # remains usable before cloud dependencies are installed.
        raise HTTPException(401, "Your session is invalid or has expired. Sign in again.") from error


def get_admin_user(user_id: UUID | None = Depends(get_current_user)) -> UUID:
    if user_id is None:
        raise HTTPException(403, "Administrator access is not available in local user mode.")
    configured = {
        value.strip().lower()
        for value in settings.admin_user_ids.split(",")
        if value.strip()
    }
    if str(user_id).lower() not in configured:
        raise HTTPException(403, "Administrator access is required.")
    return user_id
