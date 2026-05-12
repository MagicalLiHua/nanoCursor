/**
 * Vite 配置文件
 *
 * 配置功能：
 * - React 插件支持 JSX/TSX
 * - 开发服务器代理，将 /api 请求代理到 FastAPI 后端 (localhost:8100)
 * - 路径别名 @ -> src
 */

import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],

  // 开发服务器配置
  server: {
    port: 3000,
    // 将 /api 请求代理到 FastAPI 后端，避免 CORS 问题
    proxy: {
      '/api': {
        target: 'http://localhost:8100',
        changeOrigin: true,
      },
    },
  },

  // 路径别名配置
  resolve: {
    alias: {
      '@': '/src',
    },
  },
})