"""
Reseller auth dependency.
Extracts and validates Supabase JWT from the Authorization header,
returning the reseller's user ID.

Set DEV_MODE=true in .env to bypass JWT verification during local development.
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

    - DEV_MODE=true  → skip all verification, return sub claim as-is
    - SUPABASE_JWT_SECRET set → verify signature with Supabase secret
    - neither set → decode without verification (legacy dev fallback)
    """
    token = credentials.credentials
    try:
        if settings.dev_mode:
            # Dev mode fallback: assume the token is valid if it has ANY subject
            payload = jwt.decode(
                token,
                key="",
                options={"verify_signature": False, "verify_aud": False},
                algorithms=["HS256", "RS256", "ES256"],
            )
            user_id = payload.get("sub")
            if user_id: return user_id
            
        # Production: Let Supabase handle the ECC P-256 verification natively over the network
        from app.database import get_supabase
        supabase = get_supabase()
        response = supabase.auth.get_user(token)
        if response and response.user and response.user.id:
            return response.user.id
            
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token invalid or rejected by Supabase",
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid authentication token: {exc}",
        ) from exc
