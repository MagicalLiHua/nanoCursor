import React from "react";

export default function Toast({ toast }) {
  if (!toast) return null;
  return (
    <div className={`toast ${toast.kind || "info"}`} role="status">
      <strong>{toast.title || ""}</strong>
      {toast.content ? <span>{toast.content}</span> : null}
    </div>
  );
}
