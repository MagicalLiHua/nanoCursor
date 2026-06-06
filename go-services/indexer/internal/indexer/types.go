package indexer

// Symbol represents a class or function definition.
type Symbol struct {
	Name   string
	Type   string // "class" or "function"
	LineNo int
}

// Route represents an API route extracted from decorators.
type Route struct {
	Method  string
	Path    string
	Handler string
	LineNo  int
}

// FileEntry holds indexed metadata for a single file.
type FileEntry struct {
	Path      string
	Role      string // entry_point / source / test / config / doc / data
	Language  string // python / javascript / typescript / yaml / json / text
	Symbols   []Symbol
	Imports   []string
	Mtime     float64
	Size      int64
	LOC       int
	Routes    []Route
	CallGraph map[string][]string
}
