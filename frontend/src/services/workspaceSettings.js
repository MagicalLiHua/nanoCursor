export async function loadWorkspaceSettings({ fetchJson }) {
  try {
    return await fetchJson("/api/workspace/settings");
  } catch {
    return null;
  }
}

export async function saveWorkspaceSettings({ requestJson, addTimelineEvent }) {
  const model = {
    provider: document.querySelector("#settings-model-provider")?.value?.trim() || "",
    planner_model: document.querySelector("#settings-model-planner")?.value?.trim() || "",
    coder_model: document.querySelector("#settings-model-coder")?.value?.trim() || "",
  };
  const safety = {
    require_approval_for_shell: document.querySelector("#settings-safety-shell")?.checked !== false,
    require_approval_for_file_delete: document.querySelector("#settings-safety-delete")?.checked !== false,
  };
  const ignoreRaw = document.querySelector("#settings-indexing-ignore")?.value || "";
  const indexing = {
    ignore: ignoreRaw.split(",").map((item) => item.trim()).filter(Boolean),
    max_file_size_kb: parseInt(document.querySelector("#settings-indexing-maxkb")?.value || "512", 10),
  };

  try {
    const settings = await requestJson("/api/workspace/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model, safety, indexing }),
    });
    addTimelineEvent({ type: "info", title: "设置已保存", content: "工作区设置已更新。" });
    return settings;
  } catch (error) {
    addTimelineEvent({ type: "error", title: "保存设置失败", content: error.message });
    return null;
  }
}
