import { createBrowserClient, createServerClient } from '@supabase/ssr';
import { PUBLIC_SUPABASE_URL, PUBLIC_SUPABASE_ANON_KEY } from '$env/static/public';

export const supabaseBrowser = () =>
  createBrowserClient(PUBLIC_SUPABASE_URL, PUBLIC_SUPABASE_ANON_KEY);

export const supabaseServer = (cookies: {
  getAll: () => { name: string; value: string }[];
  setAll: (c: { name: string; value: string; options: Record<string, unknown> }[]) => void;
}) =>
  createServerClient(PUBLIC_SUPABASE_URL, PUBLIC_SUPABASE_ANON_KEY, { cookies });
