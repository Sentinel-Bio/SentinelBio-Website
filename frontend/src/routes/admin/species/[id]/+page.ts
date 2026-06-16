import { error } from '@sveltejs/kit';
import { adminGetSpecies } from '$lib/api';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ params }) => {
  try {
    const species = await adminGetSpecies(params.id);
    return { species };
  } catch (e) {
    throw error(404, e instanceof Error ? e.message : 'not found');
  }
};
