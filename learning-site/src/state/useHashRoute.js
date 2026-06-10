import { useEffect, useState } from "react";
import { routeToId } from "../content/contentLoader.js";

export function useHashRoute(defaultId = "") {
  const [route, setRoute] = useState(() => ({
    hash: window.location.hash,
    activeId: routeToId(window.location.hash) || defaultId,
  }));

  useEffect(() => {
    const onHashChange = () => {
      setRoute({
        hash: window.location.hash,
        activeId: routeToId(window.location.hash) || defaultId,
      });
    };
    window.addEventListener("hashchange", onHashChange);
    onHashChange();
    return () => window.removeEventListener("hashchange", onHashChange);
  }, [defaultId]);

  return [route.activeId, setRoute, route.hash];
}
