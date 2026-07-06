import asyncio
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import init_db
from app.routes import health, webhooks, failures
from app.services.scheduler import retry_sweep_loop

logging.basicConfig(level=logging.INFO)

app = FastAPI(
    title="NombaReclaim API",
    description="Failed-payment recovery engine for Nomba merchants. "
    "Built by Team Inference for the DevCareer x Nomba Hackathon 2026.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_scheduler_task = None


@app.on_event("startup")
async def on_startup():
    global _scheduler_task
    init_db()
    if settings.RECOVERY_AUTOMATION_ENABLED:
        _scheduler_task = asyncio.create_task(retry_sweep_loop())
        logging.getLogger("nombareclaim.main").info("recovery automation enabled — retry sweep loop started")
    else:
        logging.getLogger("nombareclaim.main").info("recovery automation disabled — manual dashboard trigger only")


@app.on_event("shutdown")
async def on_shutdown():
    if _scheduler_task is not None:
        _scheduler_task.cancel()


app.include_router(health.router)
app.include_router(webhooks.router)
app.include_router(failures.router)


@app.get("/")
def root():
    return {
        "service": "NombaReclaim API",
        "status": "ok",
        "docs": "/docs",
    }
