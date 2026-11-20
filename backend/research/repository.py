import psycopg
from psycopg.rows import dict_row


class PersonaMatchRepository:
    def __init__(self, database_url: str):
        self.database_url = database_url

    def get_articles_by_ids(self, ids: list[str]) -> dict[str, dict]:
        return self._fetch(
            """SELECT id, language, headline, excerpt, text_content, categories
               FROM public.articles WHERE id = ANY(%s::text[])""",
            ids,
        )

    def get_personas_by_ids(self, ids: list[str]) -> dict[str, dict]:
        return self._fetch(
            """SELECT id::text, personality_md,
                      humor_level, do_rules, dont_rules, expertise_topics
               FROM public.agent_personas WHERE id = ANY(%s::uuid[])""",
            ids,
        )

    def _fetch(self, query: str, ids: list[str]) -> dict[str, dict]:
        if not ids:
            return {}
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            return {row["id"]: row for row in connection.execute(query, (ids,))}
