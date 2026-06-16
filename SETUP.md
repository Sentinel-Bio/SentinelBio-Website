# Sentinel Bio — setup guide

Self-hosting or local dev for the Sentinel Bio research workspace.

## Prerequisites

- Linux (tested on Arch) or macOS. Works on Windows using WSL.
- Python 3.12+ (installed via [`uv`](https://docs.astral.sh/uv/))
- Node.js 20+ (for the SvelteKit frontend)
- A Supabase project (free tier is fine)
- A Redis instance (Upstash free tier works; any Redis works)

## System-level dependencies

Bioinformatics tools used by backend jobs. Some are needed only for specific
phases of the platform.

### Arch Linux

```bash
sudo pacman -S mafft iqtree
```

### Ubuntu / Debian

```bash
sudo apt install mafft iqtree
```

### macOS (Homebrew)

```bash
brew install mafft iqtree
```

## Backend

```bash
cd backend
uv sync
cp .env.example .env
# Edit .env with your Supabase URL, anon key, service key, JWT secret,
# NCBI email (for Entrez), and REDIS_URL.
```

### Database migrations

Open the Supabase SQL editor and run each migration in `supabase/migrations/`
in order. The migrations are idempotent — running them twice is safe.

Minimum required migrations for Phase 1:

- `001_init.sql`
- `002_species_lineage.sql`
- `003_projects_rename.sql`
- `004_workflow_narrative.sql`

### First admin user

After signing in via the frontend, promote your own user to admin:

```sql
update public.profiles set role = 'admin' where email = 'your-email@example.com';
```

### Run backend + worker

Two terminals:

```bash
# Terminal 1 — FastAPI
uv run fastapi dev app/main.py
```

```bash
# Terminal 2 — arq worker (for backend jobs)
uv run arq app.worker.WorkerSettings
```

## Frontend

```bash
cd frontend
npm install
cp .env.example .env
# Edit .env with your Supabase URL, anon key, and PUBLIC_BACKEND_URL
npm run dev
```

Visit http://localhost:5173.

## Troubleshooting

### `mafft: command not found`

System-level dep missing. See the platform-specific install above.

### Tree of life renders blank on home page

Usually means no species are fetched yet. Use `/admin/fetch` to fetch a taxon
(start with `1` for root cellular organisms, stop_rank=`superkingdom` to get
the three domains).


