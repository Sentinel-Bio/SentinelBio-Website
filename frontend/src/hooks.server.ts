import type { Handle } from '@sveltejs/kit';
import { supabaseServer } from '$lib/supabase';

export const handle: Handle = async ({ event, resolve }) => {
  event.locals.supabase = supabaseServer({
    getAll: () => event.cookies.getAll(),
    setAll: (list) =>
      list.forEach(({ name, value, options }) =>
        event.cookies.set(name, value, { ...options, path: '/' })
      )
  });

  const { data: { session } } = await event.locals.supabase.auth.getSession();
  event.locals.session = session;
  event.locals.user = session?.user ?? null;

  return resolve(event, {
    filterSerializedResponseHeaders: (name) => name === 'content-range'
  });
};
