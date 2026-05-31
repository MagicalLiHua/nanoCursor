import React from "react";

function BenchmarkCard({ item, onRun }) {
  return (
    <article className="benchmark-card">
      <div className="benchmark-head">
        <span className="artifact-kind">{item.category}</span>
        <span className={`badge ${item.difficulty}`}>{item.difficulty}</span>
      </div>
      <h3>{item.title}</h3>
      <p>{item.description}</p>
      <div className="benchmark-checks">
        {(item.acceptance_criteria || []).slice(0, 4).map((check, i) => <span key={i}>{check}</span>)}
      </div>
      <button className="button secondary" onClick={() => onRun?.(item.id)} type="button">运行基准</button>
    </article>
  );
}

export default function Benchmarks({ state, onRunBenchmark }) {
  return (
    <div className="benchmark-list">
      {state.benchmarks.map((item) => (
        <BenchmarkCard key={item.id} item={item} onRun={onRunBenchmark} />
      ))}
    </div>
  );
}
