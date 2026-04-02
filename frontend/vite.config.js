import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { NodeGlobalsPolyfillPlugin } from "@esbuild-plugins/node-globals-polyfill";
import { NodeModulesPolyfillPlugin } from "@esbuild-plugins/node-modules-polyfill";
import rollupNodePolyFill from "rollup-plugin-polyfill-node";

const FLASK_PORT = process.env.FLASK_PORT || process.env.FLASK_RUN_PORT || 5000;
const VITE_PORT = process.env.VITE_PORT || 5173;

export default defineConfig({
  plugins: [react()],
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: './src/test/setup.js',
    include: ['src/**/*.{test,spec}.{js,jsx,ts,tsx}'],
    coverage: {
      reporter: ['text', 'json', 'html'],
      exclude: ['node_modules/', 'src/test/'],
    },
  },
  optimizeDeps: {
    include: [
      '@emotion/react',
      '@emotion/styled',
      '@mui/material',
      '@mui/material/Tooltip',
      '@mui/material/Popper',
      '@popperjs/core',
    ],
    esbuildOptions: {
      define: {
        global: "globalThis",
      },
      plugins: [
        NodeGlobalsPolyfillPlugin({
          buffer: true,
          process: true,
          global: true,
        }),
        NodeModulesPolyfillPlugin(),
      ],
    },
  },
  build: {
    chunkSizeWarningLimit: 1000,
    rollupOptions: {
      plugins: [rollupNodePolyFill()],
      output: {
        manualChunks: {
          vendor: ['react', 'react-dom'],
          mui: ['@mui/material', '@mui/icons-material', '@emotion/react', '@emotion/styled'],
          routing: ['react-router-dom'],
          api: ['axios', 'socket.io-client'],
          utils: ['zustand', 'react-grid-layout', 'react-markdown', 'react-syntax-highlighter']
        }
      }
    },
    sourcemap: false,
    minify: 'terser',
    terserOptions: {
      compress: {
        drop_console: false,
        drop_debugger: true,
        pure_funcs: ['console.debug'],  // Only strip debug, keep error/warn/log
      },
    },
  },
  server: {
    host: '0.0.0.0',
    port: parseInt(VITE_PORT),
    strictPort: true,
    allowedHosts: [
      'localhost',
      '127.0.0.1',
      '.local',
    ],
    proxy: {
      "/api": {
        target: `http://localhost:${FLASK_PORT}`,
        changeOrigin: true,
        secure: false,
      },
      "/socket.io": {
        target: `http://localhost:${FLASK_PORT}`,
        changeOrigin: true,
        secure: false,
        ws: true,
      },
    },
  },
});
