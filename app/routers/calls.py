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
        # Since the `calls` table doesn't have reseller_id, we join with `customers`
        # and filter where customers.reseller_id == the logged in reseller
        result = (
            db.table("calls")
            .select("*, customers!inner(reseller_id)")
            .eq("customers.reseller_id", reseller_id)
            .order("started_at", desc=True)
            .execute()
        )
        # Remove the nested `customers` dict from the output for clean API responses
        calls = result.data or []
        for call in calls:
            call.pop("customers", None)
            
        return calls
    except Exception as exc:
        logger.exception("Failed to fetch calls for reseller %s: %s", reseller_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve call logs",
        ) from exc
