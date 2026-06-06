package indexer

import (
	"bufio"
	"os"
	"regexp"
	"strings"
)

var (
	rePyClass      = regexp.MustCompile(`^class\s+(\w+)`)
	rePyFunc       = regexp.MustCompile(`^(?:def\s+|async\s+def\s+)(\w+)`)
	rePyImport     = regexp.MustCompile(`^import\s+(.+)`)
	rePyFrom       = regexp.MustCompile(`^from\s+(\S+)\s+import\s+`)
	rePyRoute      = regexp.MustCompile(`@(?:\w+)\.(get|post|put|delete|patch|head|options)\s*\(\s*['"]([^'"]+)['"]`)
	rePyRoutePlain = regexp.MustCompile(`@(?:\w+)\.route\s*\(\s*['"]([^'"]+)['"]`)
	rePyCall       = regexp.MustCompile(`(\w+)\s*\(`)
)

// parsePythonFile extracts symbols, imports, routes, and call graph from a Python file.
func parsePythonFile(path string) (symbols []Symbol, imports []string, routes []Route, callGraph map[string][]string) {
	callGraph = make(map[string][]string)

	f, err := os.Open(path)
	if err != nil {
		return
	}
	defer f.Close()

	scanner := bufio.NewScanner(f)
	lineNo := 0
	currentFunc := ""
	funcCallees := make(map[string][]string)

	for scanner.Scan() {
		lineNo++
		line := scanner.Text()
		trimmed := strings.TrimSpace(line)

		isTopLevel := !strings.HasPrefix(line, " ") && !strings.HasPrefix(line, "\t")

		if isTopLevel {
			if m := rePyClass.FindStringSubmatch(trimmed); m != nil {
				symbols = append(symbols, Symbol{Name: m[1], Type: "class", LineNo: lineNo})
				currentFunc = ""
			}

			if m := rePyFunc.FindStringSubmatch(trimmed); m != nil {
				symbols = append(symbols, Symbol{Name: m[1], Type: "function", LineNo: lineNo})
				currentFunc = m[1]
			}

			if m := rePyImport.FindStringSubmatch(trimmed); m != nil {
				for _, name := range strings.Split(m[1], ",") {
					name = strings.TrimSpace(strings.Split(name, " as ")[0])
					if name != "" {
						imports = append(imports, name)
					}
				}
			}
			if m := rePyFrom.FindStringSubmatch(trimmed); m != nil {
				base := m[1]
				if idx := strings.Index(trimmed, " import "); idx >= 0 {
					names := strings.TrimSpace(trimmed[idx+8:])
					for _, name := range strings.Split(names, ",") {
						name = strings.TrimSpace(strings.Split(name, " as ")[0])
						if name != "" {
							fullName := base + "." + name
							imports = append(imports, fullName)
						}
					}
				}
			}
		}

		// Route decorators (any indentation level)
		if m := rePyRoute.FindStringSubmatch(trimmed); m != nil {
			handler := currentFunc
			if handler == "" {
				handler = "?"
			}
			routes = append(routes, Route{
				Method:  strings.ToUpper(m[1]),
				Path:    m[2],
				Handler: handler,
				LineNo:  lineNo,
			})
		}
		if m := rePyRoutePlain.FindStringSubmatch(trimmed); m != nil {
			handler := currentFunc
			if handler == "" {
				handler = "?"
			}
			routes = append(routes, Route{
				Method:  "GET",
				Path:    m[1],
				Handler: handler,
				LineNo:  lineNo,
			})
		}

		// Call graph: track function calls within functions
		if currentFunc != "" && !isTopLevel {
			for _, m := range rePyCall.FindAllStringSubmatch(trimmed, -1) {
				callee := m[1]
				if callee != currentFunc && callee != "if" && callee != "for" && callee != "while" && callee != "with" && callee != "return" && callee != "print" {
					funcCallees[currentFunc] = append(funcCallees[currentFunc], callee)
				}
			}
		}
	}

	// Deduplicate and cap call graph entries
	for funcName, callees := range funcCallees {
		seen := make(map[string]bool)
		var deduped []string
		for _, c := range callees {
			if !seen[c] {
				seen[c] = true
				deduped = append(deduped, c)
			}
		}
		if len(deduped) > 20 {
			deduped = deduped[:20]
		}
		callGraph[funcName] = deduped
	}

	return
}
