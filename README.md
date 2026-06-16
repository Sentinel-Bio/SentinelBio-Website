# Sentinel Bio

A platform for comparative genomics of non-model species, with a focus on
cancer genetics in sentinel organisms (marine mammals, etc.).

Built with SvelteKit + FastAPI + Supabase.

## Layout

- `frontend/` — SvelteKit app
- `backend/` — FastAPI service (Python, uv)
- `supabase/` — DB migrations and local dev config

## Run

Quick start:

```bash
cd backend/

uv run fastapi dev app/main.py
uv run python -m app.tool_worker
```
```bash
cd frontend/
pnpm dev
```
