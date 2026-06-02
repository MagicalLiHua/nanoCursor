import React from "react";
import { Inbox } from "lucide-react";

export default function EmptyState({ title = "暂无内容", detail = "", icon: Icon = Inbox, compact = false }) {
  return (
    <div className={`empty-state ${compact ? "compact" : ""}`}>
      <div className="empty-state-icon" aria-hidden="true">
        <Icon size={compact ? 18 : 22} />
      </div>
      <strong>{title}</strong>
      {detail && <p>{detail}</p>}
    </div>
  );
}
