import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import init_db
from app.routes import health, webhooks, failures

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


@app.on_event("startup")
def on_startup():
    init_db()


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
