import { Check, Code2, FileText, ListChecks, ListTree } from "lucide-react";

export default function ChapterOutline({ doc, progress, onToggleCompleted }) {
  if (!doc) {
    return (
      <div className="rail-card">
        <h2>学习手册</h2>
        <p>选择一篇文档后，这里会显示章节大纲、相关源码和学习状态。</p>
      </div>
    );
  }

  const completed = Boolean(progress[doc.id]?.completed);
  const scrollToHeading = (headingId) => {
    document.getElementById(headingId)?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  return (
    <div className="rail-stack">
      <section className="rail-card">
        <span className="rail-kicker">当前文档</span>
        <h2>{doc.title}</h2>
        <p>{doc.openingSummary || doc.learningGoal}</p>
        <div className="doc-facts">
          <span>{doc.groupLabel}</span>
          <span>{doc.estimatedMinutes} min</span>
          <span>{doc.difficulty}</span>
        </div>
        <button className={`complete-button ${completed ? "done" : ""}`} onClick={() => onToggleCompleted(doc.id)}>
          <Check size={16} />
          {completed ? "已读完成" : "标记已读"}
        </button>
      </section>

      {doc.learningPoints?.length ? (
        <section className="rail-card">
          <h3><ListChecks size={16} /> 本章检查点</h3>
          <div className="check-list">
            {doc.learningPoints.slice(0, 5).map((point) => (
              <span key={point}>{point}</span>
            ))}
          </div>
        </section>
      ) : null}

      <section className="rail-card">
        <h3><ListTree size={16} /> 章节大纲</h3>
        <div className="outline-list">
          {doc.headings.length ? doc.headings.slice(0, 18).map((heading) => (
            <button
              key={`${heading.id}-${heading.text}`}
              className={`outline-item level-${heading.level}`}
              type="button"
              onClick={() => scrollToHeading(heading.id)}
            >
              {heading.text}
            </button>
          )) : <p className="muted">这篇文档暂时没有标题大纲。</p>}
        </div>
      </section>

      <section className="rail-card">
        <h3><Code2 size={16} /> 相关源码</h3>
        <div className="source-list">
          {doc.sourceRefs.length ? doc.sourceRefs.map((ref) => (
            <code key={ref}>{ref}</code>
          )) : <p className="muted">未在正文中识别到源码路径。</p>}
        </div>
      </section>

      <section className="rail-card subtle">
        <h3><FileText size={16} /> 文件路径</h3>
        <code>{doc.path}</code>
      </section>
    </div>
  );
}
