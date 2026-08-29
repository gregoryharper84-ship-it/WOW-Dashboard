"""Compatibility entrypoint: governed prop self-acceptance API plus Agent Runtime V1 routes."""
from api_prod_market_acceptance import app
from agent_runtime_v1.api import router as agent_runtime_router

app.include_router(agent_runtime_router)
