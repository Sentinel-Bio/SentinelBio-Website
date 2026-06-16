import { error } from '@sveltejs/kit';
import { getProject } from '$lib/api';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ params }) => {
  try {
    const project = await getProject(params.slug);
    return { project };
  } catch (e) {
    throw error(404, e instanceof Error ? e.message : 'not found');
  }
};
