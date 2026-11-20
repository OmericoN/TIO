from fastapi import FastAPI

from api.briefing import router as briefing_router
from api.discussion import router as discussion_router
from api.health import router as health_router
from api.panel import router as panel_router
from api.profile import router as profile_router
from api.recommend import router as recommend_router
from api.recommender_lab import router as recommender_lab_router
from api.voice import router as voice_router

app = FastAPI(title="TIO API")
app.include_router(discussion_router, prefix="/api")
app.include_router(health_router, prefix="/api")
app.include_router(panel_router, prefix="/api")
app.include_router(profile_router, prefix="/api")
app.include_router(voice_router, prefix="/api")
app.include_router(recommend_router, prefix="/api")
app.include_router(briefing_router, prefix="/api")
app.include_router(recommender_lab_router)
