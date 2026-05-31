import { useEffect, useRef } from "react";
import useStore from "../store/index.js";

export function useBootstrap() {
  const mounted = useRef(false);

  useEffect(() => {
    if (mounted.current) return;
    mounted.current = true;

    const store = useStore.getState();
    store.loadWorkspaceState().finally(() => {
      store.loadWorkspaceOverview();
      store.loadRunHistory();
      store.loadRecentProjects();
      store.refreshWorkspaceData({ allowEmpty: false }).catch(() => {});
    });
  }, []);
}
