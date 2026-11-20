import json
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, JsonValue

PERSONAS_DIR = Path(__file__).resolve().parents[1] / "editorial" / "personas"
PersonaTone = Literal["formal", "conversational", "energetic", "calm", "witty", "analytical", "authoritative"]


class AgentPersona(BaseModel):
    id: UUID
    slug: str = Field(min_length=1)
    name: str = Field(min_length=1)
    personality_md: str = Field(min_length=1)
    tone: PersonaTone
    tagline: str | None = None
    description: str | None = None
    system_prompt: str | None = None
    secondary_tones: list[PersonaTone] | None = None
    target_audience: str | None = None
    expertise_topics: list[str] | None = None
    formality: int | None = Field(default=None, ge=1, le=5)
    humor_level: int | None = Field(default=None, ge=1, le=5)
    pace_wpm: int | None = Field(default=None, gt=0)
    catchphrases: list[str] | None = None
    do_rules: list[str] | None = None
    dont_rules: list[str] | None = None
    intro_template: str | None = None
    outro_template: str | None = None
    voice_provider: str | None = None
    voice_id: str | None = None
    voice_settings: JsonValue = None
    llm_model: str | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_episode_minutes: int | None = Field(default=None, gt=0)


def load_starter_personas() -> dict[UUID, AgentPersona]:
    rows = json.loads((PERSONAS_DIR / "starters.json").read_text(encoding="utf-8"))
    personas = {}
    for row in rows:
        personality_md = (PERSONAS_DIR / f"{row['slug']}.md").read_text(encoding="utf-8").strip()
        persona = AgentPersona.model_validate({**row, "personality_md": personality_md})
        if persona.id in personas:
            raise ValueError(f"Duplicate starter persona ID: {persona.id}")
        personas[persona.id] = persona
    return personas
