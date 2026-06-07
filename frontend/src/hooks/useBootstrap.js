import { useEffect, useRef } from "react";
import useStore from "../store/index.js";

export function useBootstrap() {
  const mounted = useRef(false);

  useEffect(() => {
    if (mounted.current) return;
    mounted.current = true;

    const bootstrap = async () => {
      const store = useStore.getState();
      await store.loadWorkspaceState();
      await Promise.allSettled([
        store.loadWorkspaceOverview(),
        store.loadRunHistory(),
        store.loadRecentProjects(),
        store.loadFiletoolsStatus(),
        store.loadIndexerStatus(),
        store.refreshWorkspaceData({ allowEmpty: false, includeRunState: false }),
      ]);
      await useStore.getState().restoreActiveSession();
    };
    bootstrap().catch(() => {});
  }, []);
}
