---
name: UI style preferences
description: User prefers to avoid Tailwind CSS and minimize dependency additions when building the React frontend
type: feedback
---

**Rule:** Prefer standard CSS (or CSS modules) over Tailwind CSS for styling. Avoid introducing new dependencies unless necessary.

**Why:** User explicitly said "尽量不用 tailwind 吧" (try not to use Tailwind) and "并且尽量避免引入依赖" (and try to avoid introducing dependencies).

**How to apply:** Use plain CSS files with the React components. Keep the package.json minimal — only add libraries that provide significant value (e.g., react-router-dom for routing, syntax highlighting for code). Mermaid for workflow diagram is acceptable since it was in the original Streamlit app.