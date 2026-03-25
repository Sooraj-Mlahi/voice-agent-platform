"""
FastAPI application entry point.

Lifespan manages two shared resources:
  - httpx.AsyncClient  : persistent HTTP connection pool for Retell & OpenRouter calls.
  - redis.asyncio.Redis: async Redis client for multi-instance ConversationState.

Both are stored on app.state and injected into services via helper accessors.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import httpx
import redis.asyncio as aioredis
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.routers import calls, customers, webhook
from app.routers.customers import public_router

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    # Explicit stdout so Railway's log collector does not mark startup
    # messages as errors (Railway treats any stderr output as severity=error).
    stream=__import__("sys").stdout,
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lifespan — start/stop shared resources
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("Voice Agent Platform starting up…")

    # ── Persistent HTTP client (connection pool) ──────────────────────────
    # Reuses TLS connections across requests to Retell AI and OpenRouter,
    # saving 50–200 ms per call compared to creating a new client each time.
    app.state.http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(
            connect=5.0,   # fail fast if the remote host is unreachable
            read=20.0,     # allow up to 20 s for the first byte (LLM stream)
            write=10.0,
            pool=5.0,
        ),
        limits=httpx.Limits(
            max_connections=100,
            max_keepalive_connections=20,
        ),
    )
    logger.info("HTTP client pool initialised.")

    # ── Redis client (ConversationState store) ────────────────────────────
    # Using redis.asyncio so it integrates cleanly with FastAPI's event loop.
    # decode_responses=True returns str keys/values without manual decoding.
    app.state.redis = aioredis.from_url(
        settings.redis_url,
        encoding="utf-8",
        decode_responses=True,
    )
    try:
        await app.state.redis.ping()
        logger.info("Redis connection verified at %s.", settings.redis_url)
    except Exception as exc:  # noqa: BLE001
        # Non-fatal on startup — conversation state will degrade gracefully
        # (silence prompts won't fire across workers) but the API stays alive.
        logger.warning("Redis ping failed: %s — ConversationState may not persist across workers.", exc)

    yield  # ─── application runs here ───────────────────────────────────

    logger.info("Voice Agent Platform shutting down…")
    await app.state.http_client.aclose()
    await app.state.redis.aclose()
    logger.info("HTTP client and Redis connection closed.")


# ---------------------------------------------------------------------------
# Dependency helpers — imported by services that need injected resources
# ---------------------------------------------------------------------------
def get_http_client(request: Request) -> httpx.AsyncClient:
    """FastAPI dependency: returns the shared httpx client."""
    return request.app.state.http_client


def get_redis(request: Request) -> aioredis.Redis:
    """FastAPI dependency: returns the shared Redis client."""
    return request.app.state.redis


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Voice Agent Platform API",
    description=(
        "Backend API for a multi-tenant voice agent platform powered by "
        "Retell AI, OpenRouter LLMs, and Supabase."
    ),
    version="1.1.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# ---------------------------------------------------------------------------
# CORS — adjust origins for production
# ---------------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",   # Vite dev server
        "http://localhost:3000",   # alternative local port
        "https://voice-agent-platform-front-end.vercel.app",  # production Vercel URL
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

# ---------------------------------------------------------------------------
# Global exception handler
# ---------------------------------------------------------------------------
@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error on %s %s: %s", request.method, request.url, exc)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "An internal server error occurred."},
    )

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------
app.include_router(webhook.router)
app.include_router(customers.router)
app.include_router(calls.router)
app.include_router(public_router)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------
@app.get("/health", tags=["Health"])
async def health() -> dict[str, str]:
    return {"status": "ok"}
