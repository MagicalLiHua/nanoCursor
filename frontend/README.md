# nanoCursor Frontend

这是 nanoCursor 的前后端分离 Web 工作台 MVP。

当前版本刻意保持零依赖：

- 不需要安装 React、Vite 或 UI 库。
- 可以直接运行本地静态服务器。
- 默认展示一套 Demo 数据。
- 后端启动后，可以调用 `/api/runs` 并订阅 `/api/runs/{thread_id}/events`。

## 启动

```bash
cd frontend
npm run dev
```

默认地址：

```text
http://127.0.0.1:5173
```

后端默认地址：

```text
http://127.0.0.1:8100
```

如果需要改后端地址，在浏览器控制台设置：

```js
localStorage.setItem("agenthub_api_base", "http://127.0.0.1:8100")
```

## 当前页面

- 左侧：会话列表、文件列表。
- 中间：IM 聊天区和需求输入。
- 右侧：任务板、团队状态、指标。
- 底部：事件时间线、Diff、预览、交付报告。

## 后续迁移

当前零依赖版本用于快速稳定比赛原型。等后端事件和页面交互稳定后，可以平滑迁移到：

- React + Vite + TypeScript。
- Zustand 或 Context 状态管理。
- Monaco Editor 或专用 Diff Viewer。
- lucide-react 图标库。
