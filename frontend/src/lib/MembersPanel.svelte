<script lang="ts">
  import { onMount } from 'svelte';
  import {
    listProjectMembers, inviteProjectMember, updateProjectMemberRole, removeProjectMember,
    type ProjectMember,
  } from '$lib/api';

  interface Props {
    projectSlug: string;
    isOwner: boolean;
    currentUserId: string | null;
  }
  let { projectSlug, isOwner, currentUserId }: Props = $props();

  let members = $state<ProjectMember[]>([]);
  let loading = $state(true);
  let error = $state<string | null>(null);

  // Invite form
  let inviteEmail = $state('');
  let inviteRole = $state<'editor' | 'viewer'>('editor');
  let inviting = $state(false);

  onMount(load);

  async function load() {
    loading = true;
    error = null;
    try {
      members = await listProjectMembers(projectSlug);
    } catch (e) {
      error = e instanceof Error ? e.message : 'load failed';
    }
    loading = false;
  }

  async function invite() {
    const email = inviteEmail.trim().toLowerCase();
    if (!email) return;
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
      error = 'invalid email';
      return;
    }
    inviting = true;
    error = null;
    try {
      await inviteProjectMember(projectSlug, { email, role: inviteRole });
      inviteEmail = '';
      await load();
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'invite failed';
      if (msg.includes('409')) error = 'already a member';
      else if (msg.includes('400') && msg.includes('cannot_invite_owner')) error = 'that is the owner';
      else error = msg;
    }
    inviting = false;
  }

  async function changeRole(m: ProjectMember, newRole: 'editor' | 'viewer') {
    if (m.role === newRole) return;
    try {
      await updateProjectMemberRole(projectSlug, m.id, newRole);
      await load();
    } catch (e) {
      error = e instanceof Error ? e.message : 'update failed';
    }
  }

  async function removeM(m: ProjectMember) {
    const isSelf = m.user_id && m.user_id === currentUserId;
    const msg = isSelf
      ? 'Leave this project? You will lose access.'
      : `Remove ${m.email ?? m.user_id}?`;
    if (!confirm(msg)) return;
    try {
      await removeProjectMember(projectSlug, m.id);
      if (isSelf) {
        window.location.href = '/projects';
      } else {
        await load();
      }
    } catch (e) {
      error = e instanceof Error ? e.message : 'remove failed';
    }
  }

  function fmtTime(iso: string | null): string {
    if (!iso) return 'pending';
    const d = new Date(iso);
    return d.toLocaleDateString();
  }
</script>

<div class="members">
  <div class="head">
    <h3 class="title mono">Members</h3>
    <span class="dim mono small">{members.length}</span>
  </div>

  {#if isOwner}
    <div class="invite-box">
      <input
        type="email"
        placeholder="email to invite"
        bind:value={inviteEmail}
        onkeydown={(e) => e.key === 'Enter' && invite()}
      />
      <select bind:value={inviteRole}>
        <option value="editor">editor</option>
        <option value="viewer">viewer</option>
      </select>
      <button class="primary" onclick={invite} disabled={inviting}>
        {inviting ? '…' : 'Invite'}
      </button>
    </div>
    <p class="hint mono dim small">
      Editors can run tools and upload files. Viewers can read only.
      Invitees can use the link once they sign in with Google.
    </p>
  {/if}

  {#if error}<p class="error mono small">{error}</p>{/if}

  {#if loading}
    <p class="dim mono small">loading…</p>
  {:else}
    <ul class="list">
      {#each members as m (m.id)}
        <li class="row">
          <div class="who">
            <div class="mono name">
              {m.email ?? m.user_id ?? 'unknown'}
              {#if m.is_owner_row}<span class="badge owner-badge">owner</span>{/if}
              {#if !m.accepted_at && !m.is_owner_row}<span class="badge pending">pending</span>{/if}
            </div>
            <div class="mono dim small">
              {#if m.invited_at}invited {fmtTime(m.invited_at)}{/if}
              {#if m.accepted_at && !m.is_owner_row}· accepted {fmtTime(m.accepted_at)}{/if}
            </div>
          </div>
          <div class="actions">
            {#if !m.is_owner_row}
              {#if isOwner}
                <select
                  value={m.role}
                  onchange={(e) => changeRole(m, (e.target as HTMLSelectElement).value as any)}
                >
                  <option value="editor">editor</option>
                  <option value="viewer">viewer</option>
                </select>
                <button class="mini danger" onclick={() => removeM(m)}>×</button>
              {:else if m.user_id === currentUserId}
                <span class="mono dim small">{m.role}</span>
                <button class="mini danger" onclick={() => removeM(m)}>Leave</button>
              {:else}
                <span class="mono dim small">{m.role}</span>
              {/if}
            {/if}
          </div>
        </li>
      {/each}
    </ul>
  {/if}
</div>

<style>
  .members {
    padding: 0.8rem 0;
    border-top: 1px dashed var(--border);
    margin-top: 1rem;
  }
  .head {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    margin-bottom: 0.6rem;
  }
  .title {
    margin: 0;
    font-size: 0.66rem;
    text-transform: uppercase;
    letter-spacing: 0.2em;
    color: var(--fg-dim);
  }
  .invite-box {
    display: flex;
    gap: 0.4rem;
    margin-bottom: 0.3rem;
  }
  .invite-box input {
    flex: 1;
    padding: 0.35rem 0.55rem;
    background: var(--bg-inset);
    border: 1px solid var(--border-strong);
    color: var(--fg);
    font-family: var(--font-mono);
    font-size: 0.8rem;
  }
  .invite-box select {
    padding: 0.35rem 0.5rem;
    background: var(--bg-inset);
    border: 1px solid var(--border-strong);
    color: var(--fg);
    font-family: var(--font-mono);
    font-size: 0.78rem;
  }
  .primary {
    padding: 0.35rem 0.85rem;
    background: var(--accent-dim);
    border: 1px solid var(--accent);
    color: var(--accent);
    font-family: var(--font-mono);
    font-size: 0.78rem;
  }
  .primary:hover { background: var(--accent); color: var(--bg-base); }
  .primary:disabled { opacity: 0.5; cursor: not-allowed; }

  .hint { margin: 0.5rem 0 0.8rem; }
  .list { list-style: none; padding: 0; margin: 0.6rem 0 0; }
  .row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 0.35rem 0.5rem;
    background: var(--bg-inset);
    margin-bottom: 0.25rem;
    gap: 0.5rem;
  }
  .who { min-width: 0; flex: 1; }
  .name { font-size: 0.82rem; display: flex; align-items: center; gap: 0.4rem; }
  .badge {
    font-size: 0.6rem;
    padding: 0.05rem 0.4rem;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    border: 1px solid var(--border);
    color: var(--fg-dim);
  }
  .owner-badge { border-color: var(--accent); color: var(--accent); }
  .pending { color: #e0b060; border-color: #e0b060; }
  .actions { display: flex; gap: 0.3rem; align-items: center; }
  .actions select {
    padding: 0.15rem 0.4rem;
    font-family: var(--font-mono);
    font-size: 0.7rem;
    background: var(--bg-raised);
    border: 1px solid var(--border);
    color: var(--fg-muted);
  }
  .mini {
    padding: 0.15rem 0.5rem;
    font-size: 0.7rem;
    font-family: var(--font-mono);
    background: transparent;
    border: 1px solid var(--border);
    color: var(--fg-muted);
  }
  .mini:hover { color: var(--accent); border-color: var(--accent); }
  .mini.danger:hover { color: #c93535; border-color: #c93535; }
  .error { color: #c93535; }
  .dim { color: var(--fg-dim); }
  .mono { font-family: var(--font-mono); }
  .small { font-size: 0.7rem; }
</style>
