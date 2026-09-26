import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    vue(),
    tailwindcss(),
  ],
  resolve: {
    alias: {
      '@': path.resolve(import.meta.dirname, './src'),
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8080',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    assetsDir: 'assets',
    chunkSizeWarningLimit: 1000,
    // 2026-09 改为 true：此前是 false，于是每次构建都把**上一版**的哈希 chunk 留在
    // `dist/assets` 里永不清理 —— 实测累积到 76 个 SecurityPage chunk，其中 32 个
    // 还带着**已经从源码删掉**的经纪商 code。
    //
    // 两个后果都不是"占点磁盘"那么轻：
    // ① 旧 chunk 在磁盘上**仍可被 HTTP 取到**（哈希名虽难猜，但文件确实在服务目录里），
    //    删掉的东西等于没删干净；
    // ② 部署体积随每次更新单调增长。
    //
    // `dist` 里的一切都来自 `public/` 拷贝或构建产物（已逐个核对：admin/、coins/、
    // images/、icons.svg、sitemap.xml、favicon.svg、robots.txt 均在 `public/` 有源），
    // 故清空重生成不会丢任何手工放置的文件。
    emptyOutDir: true,
  },
})
