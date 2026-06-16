import { error } from '@sveltejs/kit';
import { getSpecies } from '$lib/api';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ params, fetch }) => {
  const taxid = Number(params.taxid);
  if (!Number.isInteger(taxid) || taxid <= 0) {
    throw error(404, 'invalid taxid');
  }
  try {
    const species = await getSpecies(taxid);
    return { species };
  } catch (e) {
    throw error(404, e instanceof Error ? e.message : 'not found');
  }
};
