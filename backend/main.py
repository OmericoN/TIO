from fastapi import FastAPI

from api.discussion import router as discussion_router
from api.health import router as health_router
from api.voice import router as voice_router

app = FastAPI(title="TIO API")
app.include_router(discussion_router, prefix="/api")
app.include_router(health_router, prefix="/api")
app.include_router(voice_router, prefix="/api")
