package indexer

import (
	"path/filepath"
	"strings"
)

var entryPointNames = map[string]bool{
	"cli.py": true, "run.py": true, "main.py": true,
	"app.py": true, "__main__.py": true, "setup.py": true,
}

var configExts = map[string]bool{
	".json": true, ".yaml": true, ".yml": true,
	".toml": true, ".cfg": true, ".ini": true, ".env": true,
}

var docExts = map[string]bool{
	".md": true, ".rst": true, ".txt": true, ".markdown": true,
}

var pyExts = map[string]bool{".py": true}

var jsExts = map[string]bool{
	".js": true, ".jsx": true, ".ts": true, ".tsx": true, ".mjs": true,
}

// classifyFile determines the role of a file based on its path and name.
func classifyFile(relPath string) string {
	name := strings.ToLower(filepath.Base(relPath))
	parts := strings.Split(relPath, string(filepath.Separator))

	if entryPointNames[name] {
		return "entry_point"
	}

	for _, p := range parts {
		if p == "test" || p == "tests" || strings.HasPrefix(name, "test_") {
			return "test"
		}
	}

	ext := strings.ToLower(filepath.Ext(relPath))
	if configExts[ext] {
		return "config"
	}
	if docExts[ext] {
		return "doc"
	}

	return "source"
}

// detectLanguage returns the language identifier for a file.
func detectLanguage(ext string) string {
	switch strings.ToLower(ext) {
	case ".py":
		return "python"
	case ".js", ".mjs":
		return "javascript"
	case ".jsx":
		return "javascript"
	case ".ts", ".tsx":
		return "typescript"
	case ".json":
		return "json"
	case ".yaml", ".yml":
		return "yaml"
	case ".toml":
		return "toml"
	default:
		return "text"
	}
}
