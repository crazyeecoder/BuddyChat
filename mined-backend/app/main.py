# app/main.py
# ─────────────────────────────────────────────────────────────────────────────
# MINED — FastAPI Application Entry Point
# ─────────────────────────────────────────────────────────────────────────────

import os
from contextlib import asynccontextmanager
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from app.routers import chat
from app.services.summariser import run_nightly_summarisation

# ── Scheduler (nightly summarisation job) ────────────────────────────────────
scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Pre-load crisis detection model at startup (avoids cold start on first message)
    from app.services.crisis_service import _get_reference_embeddings
    print("[MINED] Pre-loading crisis detection model...")
    _get_reference_embeddings()
    print("[MINED] Crisis model ready.")

    # Schedule nightly summarisation at 2:00 AM
    scheduler.add_job(
        run_nightly_summarisation,
        trigger="cron",
        hour=2,
        minute=0,
        id="nightly_summarisation",
    )
    scheduler.start()
    print("[MINED] Nightly summarisation scheduler started.")

    yield

    scheduler.shutdown()


# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="MINED — BuddyChat API",
    description="Adaptive AI therapist backend. CBT/DBT/ACT grounded. RAG-powered longitudinal memory.",
    version="0.1.0",
    lifespan=lifespan,
)

# ── CORS ──────────────────────────────────────────────────────────────────────
origins = os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(chat.router, prefix="/api/v1")


@app.get("/health")
async def health():
    return {"status": "ok", "service": "MINED BuddyChat API"}
