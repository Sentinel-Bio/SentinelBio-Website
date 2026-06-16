import { PUBLIC_BACKEND_URL } from '$env/static/public';
import { supabaseBrowser } from './supabase';

/**
 * Fetch wrapper that attaches the Supabase access token if the user is logged in.
 * Throws on non-2xx with the server's error body.
 */
export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const supabase = supabaseBrowser();
  const { data: { session } } = await supabase.auth.getSession();

  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(init.headers as Record<string, string> | undefined)
  };
  if (session?.access_token) {
    headers['Authorization'] = `Bearer ${session.access_token}`;
  }

  const response = await fetch(`${PUBLIC_BACKEND_URL}${path}`, {
    ...init,
    headers
  });

  if (!response.ok) {
    let body: unknown = null;
    try { body = await response.json(); } catch { /* ignore */ }
    throw new Error(
      `${response.status}: ${JSON.stringify(body) || response.statusText}`
    );
  }
  return response.json();
}

// ─── Species ────────────────────────────────────────────────────────

export interface Species {
  id: string;
  ncbi_tax_id: number | null;
  aphia_id: number | null;
  scientific_name: string;
  common_name: string | null;
  rank: string | null;
  taxonomy: Record<string, string>;
  data: Record<string, unknown>;
  image: { url: string; attribution: string; license: string; source: string } | null;
  needs_review: boolean;
  updated_at: string;
}

export interface ProjectSpecies extends Species {
  _role?: string;
  _notes?: string;
  _added_at?: string;
  _structure_refs?: StructureRef[];
}

export async function getSpecies(taxid: number): Promise<Species> {
  return api<Species>(`/api/species/${taxid}`);
}

export async function fetchSpeciesByTaxid(taxid: number): Promise<{ species: Species; slug: string }> {
  return api(`/api/species/fetch/${taxid}`, { method: 'POST' });
}

export async function resolveName(name: string): Promise<{ taxid: number; query: string }> {
  return api(`/api/species/resolve-name`, {
    method: 'POST',
    body: JSON.stringify({ name })
  });
}

// ─── Projects ────────────────────────────────────────────────────────

export interface Project {
  id: string;
  slug: string;
  name: string;
  description: string | null;
  notes: string | null;
  owner_id: string;
  visibility: 'private' | 'unlisted' | 'public';
  research_question: string | null;
  hypotheses: string[];
  objectives: string[];
  status: 'planning' | 'active' | 'paused' | 'done' | 'archived';
  created_at: string;
  updated_at: string;
  species?: ProjectSpecies[];
  targets?: Target[];
  folder_label: string | null;
}

export async function listProjects(): Promise<Project[]> {
  return api<Project[]>('/api/projects');
}

export async function createProject(input: {
  name: string;
  description?: string;
  notes?: string;
  visibility?: 'private' | 'unlisted' | 'public';
  research_question?: string;
  hypotheses?: string[];
  objectives?: string[];
  status?: Project['status'];
}): Promise<Project> {
  return api('/api/projects', { method: 'POST', body: JSON.stringify(input) });
}

export async function getProject(slug: string): Promise<Project> {
  return api<Project>(`/api/projects/${slug}`);
}

export async function updateProject(
  slug: string,
  patch: Partial<{
    name: string;
    description: string;
    notes: string;
    visibility: 'private' | 'unlisted' | 'public';
    research_question: string;
    hypotheses: string[];
    objectives: string[];
    status: Project['status'];
  }>
): Promise<Project> {
  return api(`/api/projects/${slug}`, {
    method: 'PATCH',
    body: JSON.stringify(patch)
  });
}

export async function deleteProject(slug: string): Promise<void> {
  await api(`/api/projects/${slug}`, { method: 'DELETE' });
}

export async function addSpeciesToProject(
  slug: string,
  ref: { species_id?: string; ncbi_tax_id?: number; role?: string; notes?: string }
): Promise<void> {
  await api(`/api/projects/${slug}/species`, {
    method: 'POST',
    body: JSON.stringify(ref)
  });
}

export async function updateSpeciesRole(
  slug: string,
  speciesId: string,
  patch: { role?: string; notes?: string }
): Promise<void> {
  await api(`/api/projects/${slug}/species/${speciesId}`, {
    method: 'PATCH',
    body: JSON.stringify(patch)
  });
}

export async function removeSpeciesFromProject(
  slug: string,
  speciesId: string
): Promise<void> {
  await api(`/api/projects/${slug}/species/${speciesId}`, { method: 'DELETE' });
}

// ─── Workflow steps ──────────────────────────────────────────────────

export interface WorkflowStep {
  id: string;
  project_id: string;
  name: string;
  description: string | null;
  kind: 'manual' | 'external_tool' | 'backend_job' | 'file_upload' | 'review';
  status: 'pending' | 'in_progress' | 'blocked' | 'done' | 'skipped';
  sort_order: number;
  external_url: string | null;
  external_tool_name: string | null;
  backend_job_type: string | null;
  backend_job_params: Record<string, unknown> | null;
  backend_run_id: string | null;
  input_refs: Array<{ kind: string; ref: string; [k: string]: unknown }>;
  output_data: Record<string, unknown>;
  notes: string | null;
  created_at: string;
  updated_at: string;
}

export async function listWorkflowSteps(slug: string): Promise<WorkflowStep[]> {
  return api<WorkflowStep[]>(`/api/projects/${slug}/steps`);
}

export async function createWorkflowStep(
  slug: string,
  input: Partial<Omit<WorkflowStep, 'id' | 'project_id' | 'created_at' | 'updated_at'>> & {
    name: string;
    kind: WorkflowStep['kind'];
  }
): Promise<WorkflowStep> {
  return api(`/api/projects/${slug}/steps`, {
    method: 'POST',
    body: JSON.stringify(input)
  });
}

export async function updateWorkflowStep(
  slug: string,
  stepId: string,
  patch: Partial<Omit<WorkflowStep, 'id' | 'project_id' | 'created_at' | 'updated_at'>>
): Promise<WorkflowStep> {
  return api(`/api/projects/${slug}/steps/${stepId}`, {
    method: 'PATCH',
    body: JSON.stringify(patch)
  });
}

export async function deleteWorkflowStep(slug: string, stepId: string): Promise<void> {
  await api(`/api/projects/${slug}/steps/${stepId}`, { method: 'DELETE' });
}

export async function reorderWorkflowSteps(
  slug: string,
  stepIdsInOrder: string[]
): Promise<void> {
  await api(`/api/projects/${slug}/steps/reorder`, {
    method: 'POST',
    body: JSON.stringify({ step_ids_in_order: stepIdsInOrder })
  });
}

// ─── Narrative ───────────────────────────────────────────────────────

export async function getNarrative(slug: string): Promise<{ narrative: string }> {
  return api<{ narrative: string }>(`/api/projects/${slug}/narrative`);
}

export async function updateNarrative(slug: string, narrative: string): Promise<void> {
  await api(`/api/projects/${slug}/narrative`, {
    method: 'PUT',
    body: JSON.stringify({ narrative })
  });
}

// ─── Targets ────────────────────────────────────────────────────────

export interface Target {
  id: string;
  project_id: string;
  name: string;
  kind: 'gene' | 'protein' | 'exon' | 'region' | 'primer' | 'marker' | 'transcript' | 'domain' | 'other';
  gene_symbol: string | null;
  accession: string | null;
  region: string | null;
  species_id: string | null;
  notes: string | null;
  sort_order: number;
  created_at: string;
  updated_at: string;
}

export async function createTarget(
  slug: string,
  input: Omit<Target, 'id' | 'project_id' | 'created_at' | 'updated_at'>
): Promise<Target> {
  return api(`/api/projects/${slug}/targets`, {
    method: 'POST',
    body: JSON.stringify(input)
  });
}

export async function updateTarget(
  slug: string,
  targetId: string,
  patch: Partial<Omit<Target, 'id' | 'project_id' | 'created_at' | 'updated_at'>>
): Promise<Target> {
  return api(`/api/projects/${slug}/targets/${targetId}`, {
    method: 'PATCH',
    body: JSON.stringify(patch)
  });
}

export async function deleteTarget(slug: string, targetId: string): Promise<void> {
  await api(`/api/projects/${slug}/targets/${targetId}`, { method: 'DELETE' });
}

// ─── Project Files (server storage) ──────────────────────────────────

export interface ProjectFile {
  id: string;
  project_id: string;
  owner_id: string;
  name: string;
  storage_path: string;
  size: number;
  sha256: string | null;
  mime_hint: 'fasta' | 'fastq' | 'pdb' | 'cif' | 'newick' | 'gzip' | 'other' | string;
  source: 'upload' | 'ncbi' | 'tool';
  source_metadata: Record<string, any>;
  tool_run_id: string | null;
  species_id: string | null;
  created_at: string;
}

export async function listProjectFiles(
  slug: string,
  filters: { kind?: string; category?: string; species_id?: string } = {}
): Promise<ProjectFile[]> {
  const params = new URLSearchParams();
  if (filters.kind) params.set('kind', filters.kind);
  if (filters.category) params.set('category', filters.category);
  if (filters.species_id) params.set('species_id', filters.species_id);
  const qs = params.toString();
  return api(`/api/projects/${slug}/files${qs ? `?${qs}` : ''}`);
}

export async function uploadProjectFile(
  slug: string,
  args: { file: File; species_id?: string }
): Promise<ProjectFile> {
  const supabase = supabaseBrowser();
  const { data: { session } } = await supabase.auth.getSession();
  if (!session?.access_token) throw new Error('not authenticated');

  const fd = new FormData();
  fd.append('file', args.file);
  if (args.species_id) fd.append('species_id', args.species_id);

  const r = await fetch(`${PUBLIC_BACKEND_URL}/api/projects/${slug}/files`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${session.access_token}` },
    body: fd,
  });
  if (!r.ok) {
    let body: unknown = null;
    try { body = await r.json(); } catch {}
    throw new Error(`${r.status}: ${JSON.stringify(body) || r.statusText}`);
  }
  return r.json();
}

export async function deleteProjectFile(slug: string, fileId: string): Promise<void> {
  await api(`/api/projects/${slug}/files/${fileId}`, { method: 'DELETE' });
}

export async function getProjectFileText(
  slug: string,
  fileId: string
): Promise<{ id: string; name: string; size: number; mime_hint: string; text: string }> {
  return api(`/api/projects/${slug}/files/${fileId}/text`);
}

/** Stream-download a file: fetch with auth, blob, trigger save dialog. */
export async function downloadProjectFile(slug: string, file: ProjectFile): Promise<void> {
  const supabase = supabaseBrowser();
  const { data: { session } } = await supabase.auth.getSession();
  if (!session?.access_token) throw new Error('not authenticated');

  const r = await fetch(
    `${PUBLIC_BACKEND_URL}/api/projects/${slug}/files/${file.id}/download`,
    { headers: { Authorization: `Bearer ${session.access_token}` } }
  );
  if (!r.ok) throw new Error(`${r.status}: ${r.statusText}`);
  const blob = await r.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = file.name;
  a.click();
  URL.revokeObjectURL(url);
}

export interface QueuedJob {
  status: 'queued';
  run_id: string;
  tool: string;
  accession?: string;
}

export async function fetchNcbiAssemblyToProject(
  slug: string,
  args: { accession: string; taxid: number; species_id?: string }
): Promise<QueuedJob> {
  return api(`/api/projects/${slug}/files/fetch-ncbi-assembly`, {
    method: 'POST',
    body: JSON.stringify(args),
  });
}

export async function fetchNcbiGeneToProject(
  slug: string,
  args: { gene_symbol: string; taxid: number; species_id?: string }
): Promise<ProjectFile> {
  return api(`/api/projects/${slug}/files/fetch-ncbi-gene`, {
    method: 'POST',
    body: JSON.stringify(args),
  });
}

// ─── Admin + jobs ───────────────────────────────────────────────────

export interface Job {
  id: string;
  kind: string;
  status: 'queued' | 'running' | 'done' | 'failed';
  owner_id: string;
  params: Record<string, unknown>;
  result: Record<string, unknown> | null;
  error: string | null;
  progress: number;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  updated_at?: string;
}

export async function checkAdmin(): Promise<boolean> {
  try {
    await api('/api/admin/me');
    return true;
  } catch {
    return false;
  }
}

export interface SpeciesFilters {
  q?: string;
  rank?: string;
  kingdom?: string;
  phylum?: string;
  class?: string;
  order?: string;
  family?: string;
  genus?: string;
}

function toQuery(f: SpeciesFilters): string {
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(f)) {
    if (v && String(v).trim()) params.set(k, String(v).trim());
  }
  const s = params.toString();
  return s ? `?${s}` : '';
}

export async function listSpecies(filters: SpeciesFilters = {}): Promise<Species[]> {
  return api<Species[]>(`/api/species${toQuery(filters)}`);
}

export async function adminListSpecies(filters: SpeciesFilters = {}): Promise<Species[]> {
  return api<Species[]>(`/api/admin/species${toQuery(filters)}`);
}

export async function adminPatchSpecies(
  id: string,
  patch: Partial<{
    scientific_name: string;
    common_name: string | null;
    rank: string | null;
    taxonomy: Record<string, string>;
    data: Record<string, unknown>;
    image: Record<string, unknown> | null;
    needs_review: boolean;
  }>
): Promise<Species> {
  return api(`/api/admin/species/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(patch)
  });
}

export async function adminDeleteSpecies(id: string): Promise<void> {
  await api(`/api/admin/species/${id}`, { method: 'DELETE' });
}

export async function adminResyncSpecies(id: string): Promise<Job> {
  return api(`/api/admin/species/${id}/resync`, { method: 'POST' });
}

export async function adminFetchTaxon(input: {
  root_taxid: number;
  stop_rank: string;
  max_species?: number;
}): Promise<Job> {
  return api('/api/admin/fetch-taxon', {
    method: 'POST',
    body: JSON.stringify(input)
  });
}

export async function adminListJobs(): Promise<Job[]> {
  return api<Job[]>('/api/admin/jobs');
}

export async function adminGetJob(id: string): Promise<Job> {
  return api<Job>(`/api/admin/jobs/${id}`);
}

export async function adminResyncAllSpecies(): Promise<Job> {
  return api('/api/admin/species/resync-all', { method: 'POST' });
}

export async function adminUploadImage(file: File): Promise<{ url: string; path: string }> {
  const supabase = supabaseBrowser();
  const { data: { session } } = await supabase.auth.getSession();
  if (!session?.access_token) throw new Error('not authenticated');

  const fd = new FormData();
  fd.append('file', file);

  const r = await fetch(`${PUBLIC_BACKEND_URL}/api/admin/upload-image`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${session.access_token}` },
    body: fd
  });
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json();
}

export async function adminGetSpecies(id: string): Promise<Species> {
  return api<Species>(`/api/admin/species/${id}`);
}

export async function getDescendantsSample(taxid: number, limit = 12): Promise<Species[]> {
  return api<Species[]>(`/api/species/${taxid}/descendants?limit=${limit}`);
}

export interface GalleryCandidate {
  url: string;
  attribution: string;
  license: string;
  source: 'inaturalist' | 'wikimedia' | 'manual';
  source_url?: string;
}

export async function adminFetchGalleryCandidates(
  speciesId: string
): Promise<{ inaturalist: GalleryCandidate[]; wikimedia: GalleryCandidate[] }> {
  return api(`/api/admin/species/${speciesId}/fetch-gallery`, { method: 'POST' });
}

export async function resolveAncestors(
  names: string[]
): Promise<Record<string, { ncbi_tax_id: number; scientific_name: string; rank: string } | null>> {
  return api('/api/species/resolve-ancestors', {
    method: 'POST',
    body: JSON.stringify({ names })
  });
}

export interface TreeNode {
  name: string;
  rank: string | null;
  taxid: number | null;
  children: TreeNode[];
}

export async function getLifeTree(root = 'Eukaryota'): Promise<TreeNode> {
  return api<TreeNode>(`/api/species/tree?root=${encodeURIComponent(root)}`);
}

export async function getSubtree(taxid: number): Promise<TreeNode> {
  return api<TreeNode>(`/api/species/${taxid}/subtree`);
}

export async function adminCancelJob(id: string): Promise<void> {
  await api(`/api/admin/jobs/${id}/cancel`, { method: 'POST' });
}

export async function adminBackfillLineages(): Promise<Job> {
  return api('/api/admin/backfill-lineages', { method: 'POST' });
}

// ─── Assemblies (listing only) ──────────────────────────────────────

export interface Assembly {
  accession: string;
  name: string;
  category: string;
  level: string;
  submitter: string;
  submission_date: string;
  size_mb: number | null;
  ftp_url: string;
  type: 'GCF' | 'GCA' | 'other';
}

export async function listAssemblies(taxid: number, limit = 100): Promise<Assembly[]> {
  return api<Assembly[]>(`/api/species/${taxid}/assemblies?limit=${limit}`);
}

// ─── Traits ─────────────────────────────────────────────────────────

export interface Trait {
  label: string;
  value: string;
  unit?: string | null;
  source?: string;
}

// ─── GenBank + structures ────────────────────────────────────────────

export async function fetchGeneGenbank(
  taxid: number,
  geneSymbol: string,
): Promise<{ genbank: string; gene_symbol: string; taxid: string }> {
  return api(`/api/species/${taxid}/fetch-gene-genbank`, {
    method: 'POST',
    body: JSON.stringify({ gene_symbol: geneSymbol }),
  });
}

export async function fetchGenbankByAccession(
  accession: string,
): Promise<{ genbank: string; accession: string }> {
  return api('/api/species/fetch-genbank-accession', {
    method: 'POST',
    body: JSON.stringify({ accession }),
  });
}

export interface StructureMeta {
  pdb_id: string;
  title: string;
  organism: string;
  resolution: number | null;
  method: string;
  year: number | null;
}

export async function searchStructuresByGene(gene: string, limit = 20): Promise<StructureMeta[]> {
  return api(`/api/species/structures/search?gene=${encodeURIComponent(gene)}&limit=${limit}`);
}

export async function getStructureMeta(pdbId: string): Promise<StructureMeta> {
  return api(`/api/species/structures/${pdbId}`);
}

export interface StructureRef {
  name: string;
  source: 'upload' | 'rcsb' | 'alphafold';
  pdb_id?: string;
  subpath?: string;
  format: 'pdb' | 'cif' | 'mmcif';
  description?: string;
  added_at?: string;
}

export async function updateStructureRefs(
  slug: string,
  speciesId: string,
  refs: StructureRef[],
): Promise<void> {
  await api(`/api/projects/${slug}/species/${speciesId}/structure-refs`, {
    method: 'PUT',
    body: JSON.stringify({ structure_refs: refs }),
  });
}

// ─── Tools ──────────────────────────────────────────────────────────

export interface ToolDef {
  id: string;
  label: string;
  description: string;
  input_kind: 'fastq_upload' | 'fasta_upload' | 'aligned_fasta' | 'multi_fasta' | 'none';
  inputs: Record<string, any>;
  params: Array<{
    name: string; type: string; label: string;
    default?: any; min?: number; max?: number;
    options?: string[]; help?: string;
    kind?: string; category?: string;
  }>;
}

export interface ToolRun {
  id: string;
  project_id: string;
  tool: string;
  label: string | null;
  status: 'queued' | 'running' | 'done' | 'failed' | 'cancelled';
  progress: number;
  error: string | null;
  inputs: Record<string, any>;
  result: any | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  updated_at: string;
}

export async function listToolRegistry(): Promise<ToolDef[]> {
  return api('/api/projects/tools/registry');
}

export async function listToolRuns(slug: string): Promise<ToolRun[]> {
  return api(`/api/projects/${slug}/runs`);
}

export async function getToolRun(slug: string, runId: string): Promise<ToolRun> {
  return api(`/api/projects/${slug}/runs/${runId}`);
}

export async function deleteToolRun(slug: string, runId: string): Promise<void> {
  await api(`/api/projects/${slug}/runs/${runId}`, { method: 'DELETE' });
}

export async function createToolRun(
  slug: string,
  input: { tool: string; label?: string; inputs: Record<string, any>; params?: Record<string, any> },
): Promise<ToolRun> {
  return api(`/api/projects/${slug}/runs`, {
    method: 'POST',
    body: JSON.stringify(input),
  });
}

export async function createToolRunWithUpload(
  slug: string,
  args: {
    tool: string;
    label?: string;
    file: File;
    species_id?: string;
    params?: Record<string, any>;
  }
): Promise<ToolRun> {
  const supabase = supabaseBrowser();
  const { data: { session } } = await supabase.auth.getSession();
  if (!session?.access_token) throw new Error('not authenticated');

  const fd = new FormData();
  fd.append('file', args.file);
  fd.append('tool', args.tool);
  if (args.label) fd.append('label', args.label);
  if (args.species_id) fd.append('species_id', args.species_id);
  if (args.params) fd.append('params_json', JSON.stringify(args.params));

  const r = await fetch(`${PUBLIC_BACKEND_URL}/api/projects/${slug}/runs/upload`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${session.access_token}` },
    body: fd,
  });
  if (!r.ok) {
    let body: unknown = null;
    try { body = await r.json(); } catch {}
    throw new Error(`${r.status}: ${JSON.stringify(body) || r.statusText}`);
  }
  return r.json();
}

export async function getJob(jobId: string): Promise<{
  id: string;
  status: 'queued' | 'running' | 'done' | 'failed';
  progress: number;
  result: any;
  error: string | null;
}> {
  return api(`/api/projects/jobs/${jobId}`);
}
