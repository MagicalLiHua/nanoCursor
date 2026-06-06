package indexer

// Summary holds aggregated project statistics for display.
type Summary struct {
	TotalFiles      int
	SourceCount     int
	TestCount       int
	ConfigCount     int
	TotalLOC        int
	EntryPoints     []string
	RecentlyModified []*FileEntry
}

// RouteEntry is a flattened route with its source file information.
type RouteEntry struct {
	Method  string
	Path    string
	Handler string
	File    string
	LineNo  int
}

// RouteSummary returns all routes across all indexed files, sorted by path.
// This is a stub — the full implementation will be added in Task 6.
func (idx *ProjectIndex) RouteSummary() []RouteEntry {
	return nil
}
