export function bootstrapApp({
  render,
  loadWorkspaceState,
  loadWorkspaceOverview,
  loadRunHistory,
  loadCapabilities,
  loadMcpConfig,
  loadBenchmarks,
  loadMemoryProfile,
  loadRecoveryCenter,
  loadWorkspaceSettings,
  loadRecentProjects,
  refreshWorkspaceData,
}) {
  render();
  loadWorkspaceState().finally(() => {
    loadWorkspaceOverview();
    loadRunHistory();
    loadCapabilities();
    loadMcpConfig();
    loadBenchmarks();
    loadMemoryProfile();
    loadRecoveryCenter();
    loadWorkspaceSettings();
    loadRecentProjects();
    refreshWorkspaceData({ allowEmpty: false }).catch(() => {
      render();
    });
  });
}
