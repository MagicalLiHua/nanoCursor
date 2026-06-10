import { ArrowRight, CheckCircle2, Circle, Clock3 } from "lucide-react";
import { documentRoute } from "../content/contentLoader.js";

export default function LearningRoadmap({ documents, progress, stats, recentDoc }) {
  const chapters = documents.filter((doc) => doc.group === "chapters");
  const nextDoc = chapters.find((doc) => !progress[doc.id]?.completed) || chapters[0];

  return (
    <div className="home-view">
      <section className="home-hero">
        <div>
          <h1>把 nanoCursor 真正吃透</h1>
          <p>
            这里不是普通项目文档，而是一套围绕源码、运行链路、Agent Loop、
            上下文管理和工具治理组织起来的学习手册。
          </p>
        </div>
        <div className="hero-actions">
          {nextDoc && (
            <a className="primary-link" href={documentRoute(nextDoc)}>
              继续学习 <ArrowRight size={16} />
            </a>
          )}
          {recentDoc && (
            <a className="secondary-link" href={documentRoute(recentDoc)}>
              最近阅读：{recentDoc.title}
            </a>
          )}
        </div>
      </section>

      <section className="home-metrics">
        <div>
          <strong>{stats.percent}%</strong>
          <span>章节完成度</span>
        </div>
        <div>
          <strong>{chapters.length}</strong>
          <span>深度章节</span>
        </div>
        <div>
          <strong>4</strong>
          <span>核心专题</span>
        </div>
      </section>

      <section className="roadmap">
        <h2>推荐学习路线</h2>
        <div className="roadmap-list">
          {chapters.map((doc) => {
            const done = progress[doc.id]?.completed;
            return (
              <a key={doc.id} className="roadmap-item" href={documentRoute(doc)}>
                <span className="roadmap-state">
                  {done ? <CheckCircle2 size={18} /> : <Circle size={18} />}
                </span>
                <span className="roadmap-copy">
                  <strong>{doc.title}</strong>
                  <small>{doc.learningGoal}</small>
                </span>
                <span className="roadmap-meta">
                  <Clock3 size={14} />
                  {doc.estimatedMinutes} min
                </span>
              </a>
            );
          })}
        </div>
      </section>
    </div>
  );
}
