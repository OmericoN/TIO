from collections.abc import Mapping
from dataclasses import dataclass
from string import Template
from uuid import UUID

from pydantic import BaseModel, Field

from research.matcher import ArticleTone

from .personas import PERSONAS_DIR, AgentPersona
from .speakers import plan_speaker


class DiscussionLine(BaseModel):
    speaker: str
    text: str


class SourceExcerpt(BaseModel):
    article_id: str
    excerpt: str


class TurnContext(BaseModel):
    headline: str
    headline_article_id: str | None = None
    listener_preferences: str = ""
    question: str | None = None
    recent_turns: list[DiscussionLine] = Field(default_factory=list)
    article_tones: list[ArticleTone] = Field(default_factory=list)
    source_excerpts: list[SourceExcerpt] = Field(default_factory=list)


@dataclass(frozen=True)
class PreparedAgentTurn:
    persona: AgentPersona
    instructions: str
    input: str


def prepare_agent_turn(
    personas: Mapping[UUID, AgentPersona],
    ranked_host_ids: list[UUID],
    context: TurnContext,
    *,
    addressed_host_id: UUID | None = None,
    reply_to_host_id: UUID | None = None,
) -> PreparedAgentTurn | None:
    _, next_host_id = plan_speaker(
        ranked_host_ids, addressed_host_id=addressed_host_id, reply_to_host_id=reply_to_host_id
    )
    if next_host_id is None:
        return None

    persona = personas.get(next_host_id)
    if persona is None:
        raise ValueError(f"Selected persona is missing from the catalog: {next_host_id}")

    style = persona.model_dump_json(
        include={
            "target_audience", "expertise_topics", "formality", "humor_level",
            "pace_wpm", "catchphrases", "do_rules", "dont_rules", "intro_template", "outro_template",
        },
        exclude_none=True,
    )
    template = Template((PERSONAS_DIR.parent / "discussion_turn.md").read_text(encoding="utf-8"))
    instructions = template.substitute(
        persona_name=persona.name,
        persona_instructions=persona.system_prompt or persona.personality_md,
        delivery_settings=style,
    )
    return PreparedAgentTurn(persona=persona, instructions=instructions, input=context.model_dump_json())
