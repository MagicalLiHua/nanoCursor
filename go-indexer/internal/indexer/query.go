package indexer

import (
	"sort"
	"strings"
)

// Summary holds aggregated project statistics.
type Summary struct {
	EntryPoints      []string
	SourceCount      int
	TestCount        int
	ConfigCount      int
	TotalFiles       int
	TotalLOC         int64
	Modules          map[string]ModuleInfo
	DependencyGraph  map[string][]string
	RecentlyModified []RecentFile
	SummaryText      string
}

// ModuleInfo holds role and symbols for a module.
type ModuleInfo struct {
	Role    string
	Symbols []Symbol
}

// RecentFile holds a recently modified file path and mtime.
type RecentFile struct {
	Path  string
	Mtime float64
}

// SymbolResult holds a search result for a symbol.
type SymbolResult struct {
	File       string
	SymbolName string
	SymbolType string
	LineNo     int
}

// RouteEntry holds a route with its source file.
type RouteEntry struct {
	Method  string
	Path    string
	Handler string
	File    string
	LineNo  int
}

// SearchSymbol searches for symbols matching the query (case-insensitive substring).
func (idx *ProjectIndex) SearchSymbol(query string) []SymbolResult {
	idx.mu.RLock()
	defer idx.mu.RUnlock()

	queryLower := strings.ToLower(query)
	var results []SymbolResult

	for rel, entry := range idx.entries {
		for _, s := range entry.Symbols {
			if strings.Contains(strings.ToLower(s.Name), queryLower) {
				results = append(results, SymbolResult{
					File:       rel,
					SymbolName: s.Name,
					SymbolType: s.Type,
					LineNo:     s.LineNo,
				})
			}
		}
		if len(results) >= 20 {
			break
		}
	}

	return results
}

// SearchDependents finds files that import the given module.
func (idx *ProjectIndex) SearchDependents(module string) []string {
	idx.mu.RLock()
	defer idx.mu.RUnlock()

	moduleBase := strings.ReplaceAll(strings.ReplaceAll(module, "/", "."), ".py", "")
	var dependents []string

	for rel, entry := range idx.entries {
		for _, imp := range entry.Imports {
			if strings.Contains(imp, moduleBase) {
				dependents = append(dependents, rel)
				break
			}
		}
	}

	return dependents
}

// Summary returns a structured summary of the project.
func (idx *ProjectIndex) Summary() *Summary {
	idx.mu.RLock()
	defer idx.mu.RUnlock()

	summary := &Summary{
		Modules:         make(map[string]ModuleInfo),
		DependencyGraph: make(map[string][]string),
	}

	for rel, entry := range idx.entries {
		switch entry.Role {
		case "entry_point":
			summary.EntryPoints = append(summary.EntryPoints, rel)
		case "source":
			summary.SourceCount++
		case "test":
			summary.TestCount++
		case "config":
			summary.ConfigCount++
		}

		summary.TotalLOC += int64(entry.LOC)

		if len(entry.Symbols) > 0 {
			summary.Modules[rel] = ModuleInfo{
				Role:    entry.Role,
				Symbols: entry.Symbols,
			}
		}

		if len(entry.Imports) > 0 {
			summary.DependencyGraph[rel] = entry.Imports
		}
	}

	summary.TotalFiles = len(idx.entries)

	sort.Strings(summary.EntryPoints)

	type fileMtime struct {
		path  string
		mtime float64
	}
	var all []fileMtime
	for rel, entry := range idx.entries {
		all = append(all, fileMtime{rel, entry.Mtime})
	}
	sort.Slice(all, func(i, j int) bool {
		return all[i].mtime > all[j].mtime
	})
	for i, fm := range all {
		if i >= 5 {
			break
		}
		summary.RecentlyModified = append(summary.RecentlyModified, RecentFile{
			Path:  fm.path,
			Mtime: fm.mtime,
		})
	}

	summary.SummaryText = idx.buildSummaryText(summary)

	return summary
}

// RouteSummary returns all routes from indexed entries.
func (idx *ProjectIndex) RouteSummary() []RouteEntry {
	idx.mu.RLock()
	defer idx.mu.RUnlock()

	var allRoutes []RouteEntry
	for rel, entry := range idx.entries {
		for _, r := range entry.Routes {
			allRoutes = append(allRoutes, RouteEntry{
				Method:  r.Method,
				Path:    r.Path,
				Handler: r.Handler,
				File:    rel,
				LineNo:  r.LineNo,
			})
		}
	}

	sort.Slice(allRoutes, func(i, j int) bool {
		if allRoutes[i].Path != allRoutes[j].Path {
			return allRoutes[i].Path < allRoutes[j].Path
		}
		return allRoutes[i].Method < allRoutes[j].Method
	})

	return allRoutes
}

// Callers finds which functions call the given function.
func (idx *ProjectIndex) Callers(funcName string) []string {
	idx.mu.RLock()
	defer idx.mu.RUnlock()

	var callers []string
	for rel, entry := range idx.entries {
		for funcDef, callees := range entry.CallGraph {
			for _, c := range callees {
				if c == funcName {
					callers = append(callers, rel+":"+funcDef)
					break
				}
			}
		}
	}

	return callers
}
