import React from "react";
import RunInspector from "./RunInspector.jsx";

export default function ContextPanel({ state }) {
  return (
    <aside className="right-panel right-sidebar">
      <div className="right-panel-v2">
        <div className="right-panel-body">
          <RunInspector state={state} />
        </div>
      </div>
    </aside>
  );
}
