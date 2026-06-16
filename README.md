# Sentinel Bio

A platform for comparative genomics of non-model species, with a focus on
cancer genetics in sentinel organisms (marine mammals, etc.).

Built with SvelteKit + FastAPI + Supabase.

## Layout

- `frontend/` — SvelteKit app
- `backend/` — FastAPI service (Python, uv)
- `supabase/` — DB migrations and local dev config

## Run

Each package has its own README. Quick start:

```
cd backend && uv sync && uv run fastapi dev app/main.py
cd frontend && pnpm install && pnpm dev
```
