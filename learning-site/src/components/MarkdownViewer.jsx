import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import { Clock3, FileText, Target } from "lucide-react";

function slugify(text = "") {
  return String(text)
    .trim()
    .toLowerCase()
    .replace(/[`*_~()[\]{}:：，。,.!?/\\|]+/g, "")
    .replace(/\s+/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "");
}

function textFromChildren(children) {
  return Array.isArray(children)
    ? children.map((child) => (typeof child === "string" ? child : "")).join("")
    : String(children || "");
}

function heading(level) {
  const Tag = `h${level}`;
  return function Heading({ children }) {
    const id = slugify(textFromChildren(children));
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
          h1: heading(1),
          h2: heading(2),
          h3: heading(3),
          h4: heading(4),
          a({ href, children }) {
            const external = href?.startsWith("http");
            return (
              <a href={href} target={external ? "_blank" : undefined} rel={external ? "noreferrer" : undefined}>
                {children}
              </a>
            );
          },
        }}
      >
        {doc.body.replace(/^#\s+.+\n+/, "")}
      </ReactMarkdown>
    </article>
  );
}
