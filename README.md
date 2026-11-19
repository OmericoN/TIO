# TIO

The backend uses Python 3.12, uv, and FastAPI. The frontend uses Bun, React, TypeScript, Vite, and Tailwind CSS. Install uv, Bun, and Node.js 22.12 or newer in the 22.x line (or a newer supported LTS release).

## Local development

Run the backend in one terminal from the repository root.

```sh
cd backend
uv sync --locked
uv run --locked uvicorn main:app --reload
```

Run the frontend in a second terminal from the repository root.

```sh
cd frontend
bun install --frozen-lockfile
bun run dev
```

Vite prints the frontend URL, typically `http://localhost:5173`. It forwards `/api` requests to the backend at `http://127.0.0.1:8000`. Try `/api/health` on either server for a health check, or open `http://127.0.0.1:8000/docs` for the interactive API docs.

Run `uv run --locked python -m unittest` in `backend/` to check the health endpoint and `bun run build` in `frontend/` to type-check and build the frontend.
