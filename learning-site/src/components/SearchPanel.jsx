import { Search, X } from "lucide-react";
import { documentRoute } from "../content/contentLoader.js";

export default function SearchPanel({ query, results, onClose }) {
  if (!query.trim()) return null;
  return (
    <div className="search-panel">
      <div className="search-panel-head">
        <span><Search size={16} /> 搜索结果</span>
        <button onClick={onClose} type="button" aria-label="关闭搜索">
          <X size={16} />
        </button>
      </div>
      {results.length ? (
        <div className="search-results">
          {results.map(({ doc, excerpt }) => (
            <a key={doc.id} className="search-result" href={documentRoute(doc)} onClick={onClose}>
              <strong>{doc.title}</strong>
              <span>{doc.groupLabel} · {doc.path}</span>
              <p>{excerpt}</p>
            </a>
          ))}
        </div>
      ) : (
        <p className="search-empty">没有找到相关内容。可以换一个关键词，比如 Agent、MCP、file_ops。</p>
      )}
    </div>
  );
}
