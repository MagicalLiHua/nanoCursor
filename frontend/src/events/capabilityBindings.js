export function bindCapabilityEvents(context) {
  const {
    state,
    render,
    saveLayoutPreference,
    validateMcpConfig,
    loadMcpTools,
    installMcpPreset,
    saveMcpServerConfig,
    loadSkillDetail,
    saveSkillContent,
    cancelSkillEdit,
    deleteSkill,
    importCustomSkill,
  } = context;

  document.querySelector("[data-action='show-capabilities']")?.addEventListener("click", () => {
    state.rightTab = "capabilities";
    state.layout.rightCollapsed = false;
    saveLayoutPreference();
    render();
  });

  document.querySelectorAll("[data-action='validate-mcp']").forEach((button) => {
    button.addEventListener("click", async () => {
      const serverId = button.dataset.serverId;
      await validateMcpConfig(serverId);
    });
  });

  document.querySelectorAll("[data-action='load-mcp-tools']").forEach((button) => {
    button.addEventListener("click", async () => {
      const serverId = button.dataset.serverId;
      await loadMcpTools(serverId, true);
    });
  });

  document.querySelectorAll("[data-action='install-mcp-preset']").forEach((button) => {
    button.addEventListener("click", async () => {
      await installMcpPreset(button.dataset.presetId);
    });
  });

  document.querySelectorAll("[data-action='prepare-mcp-config']").forEach((button) => {
    button.addEventListener("click", () => {
      const rawId = button.dataset.serverId || "";
      const serverName = rawId.startsWith("mcp.") ? rawId.slice(4) : rawId;
      document.querySelector("#mcp-server-name-input")?.focus();
      const nameInput = document.querySelector("#mcp-server-name-input");
      if (nameInput && !nameInput.value.trim()) {
        nameInput.value = serverName || button.dataset.serverName || "";
      }
    });
  });

  document.querySelector("#mcp-config-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    await saveMcpServerConfig();
  });

  document.querySelectorAll("[data-action='skill-detail']").forEach((button) => {
    button.addEventListener("click", async () => {
      const skillId = button.dataset.skillId;
      await loadSkillDetail(skillId);
    });
  });

  document.querySelector("[data-action='skill-back']")?.addEventListener("click", () => {
    state.skillDetail = null;
    state.skillEditing = false;
    render();
  });

  document.querySelector("[data-action='skill-edit']")?.addEventListener("click", () => {
    state.skillEditing = true;
    render();
  });

  document.querySelector("[data-action='skill-save']")?.addEventListener("click", async () => {
    await saveSkillContent();
  });

  document.querySelector("[data-action='skill-cancel']")?.addEventListener("click", () => {
    cancelSkillEdit();
  });

  document.querySelector("[data-action='skill-delete']")?.addEventListener("click", async () => {
    if (state.skillDetail?.id) await deleteSkill(state.skillDetail.id);
  });

  document.querySelector("#skill-import-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    await importCustomSkill();
  });
}
