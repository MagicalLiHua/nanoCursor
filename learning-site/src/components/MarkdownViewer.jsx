import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import { Clock3, FileText, Target } from "lucide-react";
import MermaidDiagram from "./MermaidDiagram.jsx";

function slugify(text = "") {
  return String(text)
    .trim()
    .toLowerCase()
    .replace(/[`*_~()[\]{}:：，。,.!?/\\|]+/g, "")
    .replace(/\s+/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "");
}

function uniqueSlug(base, counts) {
  const fallback = base || "section";
  const seen = counts.get(fallback) || 0;
  counts.set(fallback, seen + 1);
  return seen === 0 ? fallback : `${fallback}-${seen + 1}`;
}

function textFromChildren(children) {
  return Array.isArray(children)
    ? children.map((child) => (typeof child === "string" ? child : "")).join("")
    : String(children || "");
}

function heading(level, counts) {
  const Tag = `h${level}`;
  return function Heading({ children }) {
    const id = uniqueSlug(slugify(textFromChildren(children)), counts);
    return <Tag id={id}>{children}</Tag>;
  };
}

export default function MarkdownViewer({ doc }) {
  if (!doc) {
    return (
      <div className="empty-state">
        <h1>没有找到文档</h1>
        <p>请选择左侧目录中的章节，或者回到学习路线。</p>
      </div>
    );
  }

  const headingCounts = new Map();

  return (
    <article className="markdown-document">
      <header className="doc-header">
        <span className="doc-kicker">{doc.groupLabel} / {doc.difficulty}</span>
        <h1>{doc.title}</h1>
        <p>{doc.learningGoal}</p>
        <div className="doc-header-meta">
          <span><Clock3 size={14} /> {doc.estimatedMinutes} min</span>
          <span><FileText size={14} /> {doc.path}</span>
        </div>
        {doc.learningPoints?.length ? (
          <div className="doc-goals">
            <strong><Target size={15} /> 学完要能回答</strong>
            <div>
              {doc.learningPoints.map((point) => <span key={point}>{point}</span>)}
            </div>
          </div>
        ) : null}
      </header>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeHighlight]}
        components={{
          h1: heading(1, headingCounts),
          h2: heading(2, headingCounts),
          h3: heading(3, headingCounts),
          h4: heading(4, headingCounts),
          a({ href, children }) {
            const external = href?.startsWith("http");
            return (
              <a href={href} target={external ? "_blank" : undefined} rel={external ? "noreferrer" : undefined}>
                {children}
              </a>
            );
          },
          code({ inline, className, children, ...props }) {
            const language = /language-(\w+)/.exec(className || "")?.[1];
            if (!inline && language === "mermaid") {
              return <MermaidDiagram chart={String(children)} />;
            }
            return (
              <code className={className} {...props}>
                {children}
              </code>
            );
          },
        }}
      >
        {doc.body.replace(/^#\s+.+\n+/, "")}
      </ReactMarkdown>
    </article>
  );
}
