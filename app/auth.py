"""
Reseller auth dependency.
Extracts and validates Supabase JWT from the Authorization header,
returning the reseller's user ID.
"""
from __future__ import annotations

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from app.config import settings

_bearer = HTTPBearer(auto_error=True)


async def get_current_reseller(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
) -> str:
    """
    Validate the Bearer JWT and return the reseller's user ID (sub claim).

    Falls back to decoding without verification when SUPABASE_JWT_SECRET is
    not configured — useful during local development.
    """
    token = credentials.credentials
    try:
        if settings.supabase_jwt_secret:
            payload = jwt.decode(
                token,
                settings.supabase_jwt_secret,
                algorithms=["HS256"],
                options={"verify_aud": False},
            )
        else:
            # Dev mode: decode without verification
            payload = jwt.decode(
                token,
                options={"verify_signature": False, "verify_aud": False},
                algorithms=["HS256"],
            )
        user_id: str | None = payload.get("sub")
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token missing subject claim",
            )
        return user_id
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid authentication token: {exc}",
        ) from exc
