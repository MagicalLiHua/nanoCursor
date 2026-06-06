package indexer

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
)

// ProjectIndex is the core indexer that scans a workspace and builds a file index.
type ProjectIndex struct {
	workspace string
	entries   map[string]*FileEntry
	mu        sync.RWMutex
}

// NewProjectIndex creates a new indexer for the given workspace.
func NewProjectIndex(workspace string) *ProjectIndex {
	return &ProjectIndex{
		workspace: workspace,
		entries:   make(map[string]*FileEntry),
	}
}

// Workspace returns the workspace path.
func (idx *ProjectIndex) Workspace() string {
	return idx.workspace
}

// Entries returns a copy of the entries map.
func (idx *ProjectIndex) Entries() map[string]*FileEntry {
	idx.mu.RLock()
	defer idx.mu.RUnlock()
	out := make(map[string]*FileEntry, len(idx.entries))
	for k, v := range idx.entries {
		cp := *v
		out[k] = &cp
	}
	return out
}

// IndexPath returns the path to the persisted index JSON file.
func (idx *ProjectIndex) IndexPath() string {
	return filepath.Join(idx.workspace, ".nanocursor", "project_index.json")
}

// skipDirs are directories to skip during scanning.
var skipDirs = map[string]bool{
	".git": true, ".venv": true, "venv": true, "__pycache__": true, "node_modules": true,
	".memory": true, ".tasks": true, ".team": true, ".snapshots": true,
	".transcripts": true, ".task_outputs": true, ".runtime-tasks": true,
	".nanocursor": true, "workspace": true,
}

// skipExts are file extensions to skip.
var skipExts = map[string]bool{
	".pyc": true, ".pyo": true, ".so": true, ".dll": true, ".exe": true,
	".bin": true, ".zip": true, ".tar": true, ".gz": true,
	".png": true, ".jpg": true, ".jpeg": true, ".gif": true, ".svg": true,
	".ico": true, ".woff": true, ".woff2": true,
}

// Build performs a full scan and builds the index. Returns (built, fileCount, error).
func (idx *ProjectIndex) Build(force bool) (bool, int, error) {
	if !force {
		if _, err := os.Stat(idx.IndexPath()); err == nil {
			if err := idx.load(); err == nil {
				return false, len(idx.entries), nil
			}
		}
	}

	idx.mu.Lock()
	idx.entries = make(map[string]*FileEntry)
	idx.mu.Unlock()

	files := idx.scannableFiles()

	type result struct {
		rel   string
		entry *FileEntry
	}

	results := make(chan result, len(files))
	var wg sync.WaitGroup

	sem := make(chan struct{}, 64)

	for _, fp := range files {
		wg.Add(1)
		go func(fp string) {
			defer wg.Done()
			sem <- struct{}{}
			defer func() { <-sem }()

			rel, entry := idx.indexFile(fp)
			if entry != nil {
				results <- result{rel: rel, entry: entry}
			}
		}(fp)
	}

	go func() {
		wg.Wait()
		close(results)
	}()

	for r := range results {
		idx.mu.Lock()
		idx.entries[r.rel] = r.entry
		idx.mu.Unlock()
	}

	if err := idx.save(); err != nil {
		return true, len(idx.entries), fmt.Errorf("save index: %w", err)
	}

	return true, len(idx.entries), nil
}

// Update performs an incremental update. Returns (updatedCount, removedCount, error).
func (idx *ProjectIndex) Update() (int, int, error) {
	if _, err := os.Stat(idx.IndexPath()); os.IsNotExist(err) {
		_, count, err := idx.Build(false)
		return count, 0, err
	}

	if err := idx.load(); err != nil {
		return 0, 0, err
	}

	currentFiles := make(map[string]string)
	for _, fp := range idx.scannableFiles() {
		rel, _ := filepath.Rel(idx.workspace, fp)
		currentFiles[rel] = fp
	}

	updated := 0
	for rel, fp := range currentFiles {
		info, err := os.Stat(fp)
		if err != nil {
			continue
		}
		mtime := float64(info.ModTime().UnixNano()) / 1e9

		idx.mu.RLock()
		existing, exists := idx.entries[rel]
		idx.mu.RUnlock()

		if !exists || existing.Mtime < mtime {
			_, entry := idx.indexFile(fp)
			if entry != nil {
				idx.mu.Lock()
				idx.entries[rel] = entry
				idx.mu.Unlock()
				updated++
			}
		}
	}

	removed := 0
	idx.mu.Lock()
	for rel := range idx.entries {
		if _, ok := currentFiles[rel]; !ok {
			delete(idx.entries, rel)
			removed++
		}
	}
	idx.mu.Unlock()

	if updated > 0 || removed > 0 {
		if err := idx.save(); err != nil {
			return updated, removed, fmt.Errorf("save index: %w", err)
		}
	}

	return updated, removed, nil
}

// scannableFiles returns all files that should be indexed.
func (idx *ProjectIndex) scannableFiles() []string {
	var files []string

	filepath.Walk(idx.workspace, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return nil
		}

		if info.IsDir() {
			name := info.Name()
			if skipDirs[name] || strings.HasPrefix(name, ".") {
				return filepath.SkipDir
			}
			return nil
		}

		ext := strings.ToLower(filepath.Ext(path))
		if skipExts[ext] {
			return nil
		}

		files = append(files, path)
		return nil
	})

	return files
}

// indexFile indexes a single file and returns its relative path and entry.
func (idx *ProjectIndex) indexFile(absPath string) (string, *FileEntry) {
	info, err := os.Stat(absPath)
	if err != nil {
		return "", nil
	}

	rel, _ := filepath.Rel(idx.workspace, absPath)
	ext := strings.ToLower(filepath.Ext(absPath))
	role := classifyFile(rel)
	language := detectLanguage(ext)

	var symbols []Symbol
	var imports []string
	var routes []Route
	var callGraph map[string][]string

	switch {
	case pyExts[ext]:
		symbols, imports, routes, callGraph = parsePythonFile(absPath)
	case jsExts[ext]:
		symbols, imports, routes, callGraph = parseJavaScriptFile(absPath)
	}

	loc := countLOC(absPath, language)

	return rel, &FileEntry{
		Path:      rel,
		Role:      role,
		Language:  language,
		Symbols:   symbols,
		Imports:   imports,
		Mtime:     float64(info.ModTime().UnixNano()) / 1e9,
		Size:      info.Size(),
		LOC:       loc,
		Routes:    routes,
		CallGraph: callGraph,
	}
}

// save persists the index to disk as JSON.
func (idx *ProjectIndex) save() error {
	idx.mu.RLock()
	defer idx.mu.RUnlock()

	dir := filepath.Dir(idx.IndexPath())
	if err := os.MkdirAll(dir, 0755); err != nil {
		return err
	}

	data := make(map[string]interface{})
	for rel, entry := range idx.entries {
		data[rel] = entry
	}

	f, err := os.Create(idx.IndexPath())
	if err != nil {
		return err
	}
	defer f.Close()

	enc := json.NewEncoder(f)
	enc.SetIndent("", "  ")
	return enc.Encode(data)
}

// load reads the index from disk.
func (idx *ProjectIndex) load() error {
	f, err := os.Open(idx.IndexPath())
	if err != nil {
		return err
	}
	defer f.Close()

	var raw map[string]*FileEntry
	if err := json.NewDecoder(f).Decode(&raw); err != nil {
		return err
	}

	idx.mu.Lock()
	idx.entries = raw
	idx.mu.Unlock()

	return nil
}

// buildSummaryText generates a markdown summary of the project.
func (idx *ProjectIndex) buildSummaryText(summary *Summary) string {
	var lines []string
	lines = append(lines, fmt.Sprintf("项目: %s", filepath.Base(idx.workspace)))

	if len(summary.EntryPoints) > 0 {
		lines = append(lines, fmt.Sprintf("入口: %s", strings.Join(summary.EntryPoints, ", ")))
	} else {
		lines = append(lines, "入口: unknown")
	}

	lines = append(lines, fmt.Sprintf("文件: %d 个 (%d source, %d test, %d config)",
		summary.TotalFiles, summary.SourceCount, summary.TestCount, summary.ConfigCount))
	lines = append(lines, fmt.Sprintf("代码行数: %d 行", summary.TotalLOC))

	if len(summary.RecentlyModified) > 0 {
		var recent []string
		for i, rf := range summary.RecentlyModified {
			if i >= 3 {
				break
			}
			recent = append(recent, rf.Path)
		}
		lines = append(lines, fmt.Sprintf("最近修改: %s", strings.Join(recent, ", ")))
	}

	routes := idx.RouteSummary()
	if len(routes) > 0 {
		lines = append(lines, fmt.Sprintf("\n【API 路由】(%d 个端点)", len(routes)))
		for i, r := range routes {
			if i >= 20 {
				lines = append(lines, fmt.Sprintf("  ... 及其他 %d 个路由", len(routes)-20))
				break
			}
			lines = append(lines, fmt.Sprintf("  %-6s %-30s -> %s  (%s:%d)",
				r.Method, r.Path, r.Handler, r.File, r.LineNo))
		}
	}

	return strings.Join(lines, "\n")
}
