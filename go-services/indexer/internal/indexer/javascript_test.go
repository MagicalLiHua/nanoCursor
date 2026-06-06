package indexer

import (
	"os"
	"path/filepath"
	"testing"
)

func TestParseJavaScriptFile(t *testing.T) {
	dir := t.TempDir()
	jsFile := filepath.Join(dir, "server.js")
	content := `import express from 'express';
const utils = require('./utils');

class ApiController {
    handle() {}
}

function processRequest() {
    return true;
}

app.get('/api/health', (req, res) => {
    res.json({ok: true});
});

router.post('/api/items', createItem);
`
	if err := os.WriteFile(jsFile, []byte(content), 0644); err != nil {
		t.Fatal(err)
	}

	symbols, imports, routes, _ := parseJavaScriptFile(jsFile)

	if len(imports) < 2 {
		t.Errorf("expected at least 2 imports, got %d: %v", len(imports), imports)
	}

	foundClass := false
	foundFunc := false
	for _, s := range symbols {
		if s.Name == "ApiController" && s.Type == "class" {
			foundClass = true
		}
		if s.Name == "processRequest" && s.Type == "function" {
			foundFunc = true
		}
	}
	if !foundClass {
		t.Error("expected to find class ApiController")
	}
	if !foundFunc {
		t.Error("expected to find function processRequest")
	}

	if len(routes) < 2 {
		t.Errorf("expected at least 2 routes, got %d", len(routes))
	}

	t.Logf("symbols: %+v", symbols)
	t.Logf("imports: %+v", imports)
	t.Logf("routes: %+v", routes)
}
