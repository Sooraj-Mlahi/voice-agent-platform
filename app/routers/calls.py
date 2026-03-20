"""
GET  /api/calls — list all call logs for the logged-in reseller
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from app.auth import get_current_reseller
from app.database import get_supabase

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["Calls"])


@router.get("/calls", response_model=list[dict[str, Any]])
async def list_calls(
    reseller_id: str = Depends(get_current_reseller),
) -> list[dict[str, Any]]:
    """Return all call logs belonging to the authenticated reseller."""
    try:
        db = get_supabase()
        result = (
            db.table("call_logs")
            .select("*")
            .eq("reseller_id", reseller_id)
            .order("created_at", desc=True)
            .execute()
        )
        return result.data or []
    except Exception as exc:
        logger.exception("Failed to fetch calls for reseller %s: %s", reseller_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve call logs",
        ) from exc
