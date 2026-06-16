-- Run this in the Supabase SQL editor.
-- Adds a per-run log buffer that tools stream into and the frontend renders as a
-- live console. Nullable + default empty so existing rows and any tool that
-- doesn't log still work.

alter table public.tool_runs
  add column if not exists logs text default '';

-- (Optional) If you want failed runs' tracebacks out of `error` eventually,
-- they already live in `logs` too now. No data migration needed.
