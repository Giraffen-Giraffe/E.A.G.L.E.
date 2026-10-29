import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 本番ビルドは Python 側(eagle/web/static)へ出力し、API サーバーがそのまま配信する。
// 開発時は `npm run dev` で起動し、/api はデスクトップ版の既定ポートへ中継する。
export default defineConfig({
  plugins: [react()],
  build: { outDir: "../eagle/web/static", emptyOutDir: true, chunkSizeWarningLimit: 1200 },
  server: { port: 5173, proxy: { "/api": "http://127.0.0.1:18760" } },
});
