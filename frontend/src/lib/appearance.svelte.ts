/**
 * User preference for light/dark. Persists in localStorage.
 * Only applies to the neutral theme; biomarine ignores it.
 *
 * Uses Svelte 5 runes in a module-scoped class — simpler than stores
 * for a global singleton.
 */
class AppearanceState {
  mode = $state<'light' | 'dark'>('light');

  constructor() {
    if (typeof window !== 'undefined') {
      const saved = localStorage.getItem('appearance');
      if (saved === 'dark' || saved === 'light') {
        this.mode = saved;
      } else {
        // Honor system preference on first visit.
        this.mode = window.matchMedia('(prefers-color-scheme: dark)').matches
          ? 'dark'
          : 'light';
      }
    }
  }

  toggle() {
    this.mode = this.mode === 'dark' ? 'light' : 'dark';
    if (typeof window !== 'undefined') {
      localStorage.setItem('appearance', this.mode);
    }
  }
}

export const appearance = new AppearanceState();
