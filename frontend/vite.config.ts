import { defineConfig } from "vite";

export default defineConfig({
  server: {
    port: 5173,
    // Forward API calls to the local Flask backend during development.
    proxy: {
      "/api": "http://127.0.0.1:5000",
    },
  },
});
