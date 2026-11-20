from fastapi import FastAPI

from api.health import router as health_router
from api.voice import router as voice_router

app = FastAPI(title="TIO API")
app.include_router(health_router, prefix="/api")
app.include_router(voice_router, prefix="/api")
