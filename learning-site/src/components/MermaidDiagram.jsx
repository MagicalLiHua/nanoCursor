import { useEffect, useId, useMemo, useState } from "react";

function normalizeSource(source = "") {
  return String(source).replace(/\n$/, "").trim();
}

function safeId(id) {
  return `mermaid-${id.replace(/[^a-zA-Z0-9_-]/g, "")}`;
}

export default function MermaidDiagram({ chart }) {
  const reactId = useId();
  const diagramId = useMemo(() => safeId(reactId), [reactId]);
  const source = useMemo(() => normalizeSource(chart), [chart]);
  const [svg, setSvg] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;

    async function renderDiagram() {
      if (!source) return;
      try {
        const mermaid = (await import("mermaid")).default;
        mermaid.initialize({
          startOnLoad: false,
          securityLevel: "strict",
          theme: "neutral",
          flowchart: {
            curve: "basis",
            htmlLabels: false,
          },
        });
        const result = await mermaid.render(diagramId, source);
        if (!cancelled) {
          setSvg(result.svg);
          setError("");
        }
      } catch (err) {
        if (!cancelled) {
          setSvg("");
          setError(err instanceof Error ? err.message : String(err));
        }
      }
    }

    renderDiagram();

    return () => {
      cancelled = true;
    };
  }, [diagramId, source]);

  if (error) {
    return (
      <div className="mermaid-diagram mermaid-diagram--error">
        <strong>结构图渲染失败</strong>
        <pre>{source}</pre>
        <p>{error}</p>
      </div>
    );
  }

  return (
    <div className="mermaid-diagram">
      {svg ? <div dangerouslySetInnerHTML={{ __html: svg }} /> : <span>正在渲染结构图...</span>}
    </div>
  );
}
