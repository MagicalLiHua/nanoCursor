import { useCallback, useEffect, useMemo, useState } from "react";
import AppShell from "./components/AppShell.jsx";
import ChapterOutline from "./components/ChapterOutline.jsx";
import LearningRoadmap from "./components/LearningRoadmap.jsx";
import MarkdownViewer from "./components/MarkdownViewer.jsx";
import SearchPanel from "./components/SearchPanel.jsx";
import { documentRoute, firstChapter, loadDocuments } from "./content/contentLoader.js";
import { searchDocuments } from "./content/searchIndex.js";
import { useHashRoute } from "./state/useHashRoute.js";
import { useReadingProgress } from "./state/useReadingProgress.js";

const documents = loadDocuments();

const LAYOUT_KEY = "nanocursor.learning.layout";
function loadLayoutPrefs() {
  try { return JSON.parse(localStorage.getItem(LAYOUT_KEY) || "{}"); } catch { return {}; }
}
function saveLayoutPrefs(value) {
  localStorage.setItem(LAYOUT_KEY, JSON.stringify(value));
}

export default function App() {
  const defaultDoc = firstChapter(documents);
  const [activeId, , currentHash] = useHashRoute(defaultDoc?.id || "");
  const [searchQuery, setSearchQuery] = useState("");
  const { progress, stats, markVisited, toggleCompleted } = useReadingProgress(documents);
  const [prefs, setPrefs] = useState(loadLayoutPrefs);
  const sidebarCollapsed = prefs.sidebarCollapsed || false;
  const rightRailCollapsed = prefs.rightRailCollapsed || false;

  const toggleSidebar = useCallback(() => {
    setPrefs((prev) => { const next = { ...prev, sidebarCollapsed: !prev.sidebarCollapsed }; saveLayoutPrefs(next); return next; });
  }, []);
  const toggleRightRail = useCallback(() => {
    setPrefs((prev) => { const next = { ...prev, rightRailCollapsed: !prev.rightRailCollapsed }; saveLayoutPrefs(next); return next; });
  }, []);

  const activeDoc = useMemo(
    () => documents.find((doc) => doc.id === activeId) || null,
    [activeId],
  );
  const recentDoc = documents.find((doc) => doc.id === stats.recentId);
  const searchResults = useMemo(
    () => searchDocuments(documents, searchQuery),
    [searchQuery],
  );

  useEffect(() => {
    if (activeDoc) markVisited(activeDoc.id);
  }, [activeDoc, markVisited]);

  const showHome = !activeId || currentHash === "#/" || currentHash === "";

  return (
    <AppShell
      documents={documents}
      activeDoc={activeDoc}
      progress={progress}
      stats={stats}
      searchQuery={searchQuery}
      onSearchChange={setSearchQuery}
      sidebarCollapsed={sidebarCollapsed}
      onToggleSidebar={toggleSidebar}
      rightRailCollapsed={rightRailCollapsed}
      onToggleRightRail={toggleRightRail}
      aside={(
        <ChapterOutline
          doc={activeDoc}
          progress={progress}
          onToggleCompleted={toggleCompleted}
        />
      )}
      sidebarExtra={(
        <a className="sidebar-home" href={defaultDoc ? documentRoute(defaultDoc) : "#/"}>
          从第一章开始
        </a>
      )}
    >
      <SearchPanel query={searchQuery} results={searchResults} onClose={() => setSearchQuery("")} />
      {showHome ? (
        <LearningRoadmap documents={documents} progress={progress} stats={stats} recentDoc={recentDoc} />
      ) : (
        <MarkdownViewer doc={activeDoc} />
      )}
    </AppShell>
  );
}
