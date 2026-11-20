# Backend development

See the [README](../README.md#run-locally) to start the servers.

## Backend layout

- [`backend/main.py`](../backend/main.py) creates the FastAPI app and mounts the routers under `/api`.
- [`backend/api/`](../backend/api/) holds endpoint definitions. Add new endpoints to the matching feature router and register new routers in `main.py`.
- [`backend/voice/`](../backend/voice/) holds the ElevenLabs client. The preview route uses it to request an ElevenAgents session.
- [`backend/editorial/`](../backend/editorial/framework.md) holds the written editorial framework, host persona Markdown, and listener profiles. These files are authoring references.
- [`backend/research/`](../backend/research/) holds article-to-host matching and its database and OpenAI clients.
- [`backend/tests/`](../backend/tests/) holds backend tests.

| Endpoint | Route file |
| --- | --- |
| `GET /api/health` | [`backend/api/health.py`](../backend/api/health.py) |
| `POST /api/voice/session` | [`backend/api/voice.py`](../backend/api/voice.py) |
| `POST /api/discussion/next-speaker` | [`backend/api/discussion.py`](../backend/api/discussion.py) |

## Checks

Run `uv run --locked python -m unittest` in `backend/` for backend tests. Run `bun run build` in `frontend/` to type-check and build the frontend.
