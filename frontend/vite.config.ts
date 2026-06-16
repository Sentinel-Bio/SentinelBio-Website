import tailwindcss from '@tailwindcss/vite';
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig, loadEnv } from 'vite';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  const backendProxy = env.BACKEND_PROXY || 'http://localhost:8000';

  return {
    plugins: [tailwindcss(), sveltekit()],
    server: {
      host: '0.0.0.0',
      port: 5173,
      allowedHosts: true as any,
      proxy: {
        '/api': {
          target: backendProxy,
          changeOrigin: true,
          ws: true,
        },
      },
    },
    preview: {
      host: '0.0.0.0',
      port: 4173,
      allowedHosts: true as any,
    },
  };
});
