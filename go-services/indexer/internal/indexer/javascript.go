package indexer

import (
	"bufio"
	"os"
	"regexp"
	"strings"
)

var (
	reJSImport    = regexp.MustCompile(`import\s+.*?\s+from\s+['"]([^'"]+)['"]`)
	reJSRequire   = regexp.MustCompile(`require\s*\(\s*['"]([^'"]+)['"]\s*\)`)
	reJSFunction  = regexp.MustCompile(`^(?:export\s+)?(?:async\s+)?function\s+(\w+)`)
	reJSClass     = regexp.MustCompile(`^(?:export\s+)?class\s+(\w+)`)
	reJSRoute     = regexp.MustCompile(`(?:app|router)\.(get|post|put|delete|patch)\s*\(\s*['"]([^'"]+)['"]`)
	reJSRouteTmpl = regexp.MustCompile("(?:app|router)\\.(get|post|put|delete|patch)\\s*\\(\\s*`([^`]+)`")
)

// parseJavaScriptFile extracts symbols, imports, and routes from a JS/TS file.
func parseJavaScriptFile(path string) (symbols []Symbol, imports []string, routes []Route, callGraph map[string][]string) {
	callGraph = make(map[string][]string)

	f, err := os.Open(path)
	if err != nil {
		return
	}
	defer f.Close()

	scanner := bufio.NewScanner(f)
	lineNo := 0

	for scanner.Scan() {
		lineNo++
		line := scanner.Text()
		trimmed := strings.TrimSpace(line)

		// ES6 imports
		if m := reJSImport.FindStringSubmatch(trimmed); m != nil {
			imports = append(imports, m[1])
		}

		// CommonJS require
		if m := reJSRequire.FindStringSubmatch(trimmed); m != nil {
			imports = append(imports, m[1])
		}

		// Function declarations
		if m := reJSFunction.FindStringSubmatch(trimmed); m != nil {
			symbols = append(symbols, Symbol{Name: m[1], Type: "function", LineNo: lineNo})
		}

		// Class declarations
		if m := reJSClass.FindStringSubmatch(trimmed); m != nil {
			symbols = append(symbols, Symbol{Name: m[1], Type: "class", LineNo: lineNo})
		}

		// Routes: app.get('/path') or router.post("/path")
		if m := reJSRoute.FindStringSubmatch(trimmed); m != nil {
			routes = append(routes, Route{
				Method:  strings.ToUpper(m[1]),
				Path:    m[2],
				Handler: "?",
				LineNo:  lineNo,
			})
		}
		// Template literal routes
		if m := reJSRouteTmpl.FindStringSubmatch(trimmed); m != nil {
			routes = append(routes, Route{
				Method:  strings.ToUpper(m[1]),
				Path:    m[2],
				Handler: "?",
				LineNo:  lineNo,
			})
		}
	}

	return
}
