"""
scripts/awaken_webhooks.py
==========================
One-shot utility: patch the webhook_url on every existing Retell agent
that was created before WEBHOOK_BASE_URL was configured in Railway.

Usage (run from the project root):
    python -m scripts.awaken_webhooks

Requires the same environment variables as the main app:
    RETELL_API_KEY
    SUPABASE_URL
    SUPABASE_SERVICE_KEY
    WEBHOOK_BASE_URL   ← must be set, otherwise this script is a no-op

The script is safe to re-run: patching an agent that already has the
correct webhook_url is idempotent — Retell accepts the same value twice.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys

import httpx
from supabase import create_client

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("awaken_webhooks")

# ---------------------------------------------------------------------------
# Config — pulled directly from env (no pydantic-settings dependency)
# ---------------------------------------------------------------------------
RETELL_API_KEY    = os.environ.get("RETELL_API_KEY", "")
SUPABASE_URL      = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY      = os.environ.get("SUPABASE_SERVICE_KEY", "")
WEBHOOK_BASE_URL  = os.environ.get("WEBHOOK_BASE_URL", "").rstrip("/")

RETELL_BASE_URL   = "https://api.retellai.com"
WEBHOOK_PATH      = "/webhook/retell"


def _validate_env() -> bool:
    missing = [
        name for name, val in {
            "RETELL_API_KEY":   RETELL_API_KEY,
            "SUPABASE_URL":     SUPABASE_URL,
            "SUPABASE_SERVICE_KEY": SUPABASE_KEY,
            "WEBHOOK_BASE_URL": WEBHOOK_BASE_URL,
        }.items() if not val
    ]
    if missing:
        logger.error("Missing required environment variables: %s", ", ".join(missing))
        return False
    return True


async def patch_agent_webhook(
    client: httpx.AsyncClient,
    agent_id: str,
    webhook_url: str,
) -> bool:
    """PATCH a single Retell agent's webhook_url. Returns True on success."""
    try:
        response = await client.patch(
            f"{RETELL_BASE_URL}/update-agent/{agent_id}",
            json={"webhook_url": webhook_url},
            headers={
                "Authorization": f"Bearer {RETELL_API_KEY}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(connect=5.0, read=15.0, write=10.0, pool=5.0),
        )
        if response.status_code in (200, 201):
            return True
        logger.error(
            "  Agent %s — Retell returned %s: %s",
            agent_id, response.status_code, response.text[:200],
        )
        return False
    except Exception as exc:
        logger.error("  Agent %s — HTTP error: %s", agent_id, exc)
        return False


async def main() -> None:
    if not _validate_env():
        sys.exit(1)

    target_webhook_url = f"{WEBHOOK_BASE_URL}{WEBHOOK_PATH}"
    logger.info("Target webhook URL: %s", target_webhook_url)

    # ── Fetch all customers with a retell_agent_id ──────────────────────────
    db = create_client(SUPABASE_URL, SUPABASE_KEY)
    result = (
        db.table("customers")
        .select("id, name, retell_agent_id")
        .neq("retell_agent_id", None)   # only customers that have an agent
        .execute()
    )
    customers = result.data or []

    if not customers:
        logger.info("No customers with retell_agent_id found — nothing to do.")
        return

    logger.info("Found %d customers with Retell agents.", len(customers))

    # ── Patch each agent ────────────────────────────────────────────────────
    succeeded = 0
    failed = 0
    skipped = 0

    async with httpx.AsyncClient() as http:
        for customer in customers:
            agent_id = customer.get("retell_agent_id")
            name     = customer.get("name", "unknown")

            if not agent_id:
                skipped += 1
                continue

            logger.info("  Patching agent %s  (customer: %s)…", agent_id, name)
            ok = await patch_agent_webhook(http, agent_id, target_webhook_url)

            if ok:
                logger.info("  ✓ Agent %s awakened.", agent_id)
                succeeded += 1
            else:
                failed += 1

            # Polite rate-limit: 10 req/s max against Retell
            await asyncio.sleep(0.12)

    # ── Summary ─────────────────────────────────────────────────────────────
    logger.info("─" * 60)
    logger.info("Webhook Awakening complete.")
    logger.info("  Awakened (success) : %d", succeeded)
    logger.info("  Failed             : %d", failed)
    logger.info("  Skipped (no agent) : %d", skipped)
    logger.info("  Total processed    : %d / %d", succeeded + failed, len(customers))

    if failed:
        logger.warning(
            "%d agents could not be patched — check Retell API key and agent IDs above.",
            failed,
        )
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
