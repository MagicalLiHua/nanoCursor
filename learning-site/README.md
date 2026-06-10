# nanoCursor Learning Site

这是 nanoCursor 的学习资料前端。它会读取 `learning-site/src/content/handbook/**/*.md`，把 Markdown 学习手册渲染成一个带导航、搜索、大纲和阅读进度的本地网站。

## 启动

```bash
cd learning-site
npm install
npm run dev
```

默认地址：

```text
http://127.0.0.1:5174
```

## 构建检查

```bash
npm run build
```

## 内容来源

学习资料仍然写在仓库的 Markdown 里：

```text
learning-site/src/content/handbook/
  chapters/
  maps/
  exercises/
  interview/
```

前端只负责渲染、搜索、记录阅读进度和提供学习路径。这样内容不会被锁死在 React 组件里，后续继续补章节时只需要改 Markdown。

## 设计原则

- Markdown 是知识源，React 是阅读体验。
- 每章尽量包含源码摘录、设计解释、面试追问和学习路径。
- 阅读进度只保存在浏览器 localStorage，不影响项目运行。
- 学习站和 nanoCursor 主前端分离，避免把展示文档的复杂度塞回产品界面。
