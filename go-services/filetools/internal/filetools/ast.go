package filetools

import (
	"bufio"
	"fmt"
	"os"
	"regexp"
	"strings"
)

var (
	reClassDef = regexp.MustCompile(`^class\s+(\w+)`)
	reFuncDef  = regexp.MustCompile(`^(?:async\s+)?def\s+(\w+)`)
)

// ExtractOutline returns an AST outline of a Python file.
func ExtractOutline(filepath string) (string, error) {
	f, err := os.Open(filepath)
	if err != nil {
		return "", err
	}
	defer f.Close()

	type classInfo struct {
		name    string
		line    int
		methods []string
	}

	var classes []classInfo
	var functions []string
	currentClass := -1
	lineNo := 0

	scanner := bufio.NewScanner(f)
	for scanner.Scan() {
		lineNo++
		line := scanner.Text()
		trimmed := strings.TrimSpace(line)

		if m := reClassDef.FindStringSubmatch(trimmed); m != nil {
			if currentClass >= 0 {
				classes[currentClass].line = lineNo
			}
			classes = append(classes, classInfo{name: m[1], line: lineNo})
			currentClass = len(classes) - 1
		} else if m := reFuncDef.FindStringSubmatch(trimmed); m != nil {
			if currentClass >= 0 {
				classes[currentClass].methods = append(classes[currentClass].methods,
					fmt.Sprintf("def %s (line %d)", m[1], lineNo))
			} else {
				functions = append(functions, fmt.Sprintf("def %s (line %d)", m[1], lineNo))
			}
		}
	}

	totalLines := lineNo
	var parts []string
	for _, c := range classes {
		if len(c.methods) > 0 {
			parts = append(parts, fmt.Sprintf("class %s (line %d):\n    %s",
				c.name, c.line, strings.Join(c.methods, "\n    ")))
		} else {
			parts = append(parts, fmt.Sprintf("class %s (line %d)", c.name, c.line))
		}
	}
	for _, f := range functions {
		parts = append(parts, f)
	}

	if len(parts) == 0 {
		return "(无函数或类定义)", nil
	}

	return fmt.Sprintf("[文件结构大纲] 共 %d 行\n%s\n\n提示：使用 read_function 工具读取特定函数的完整代码。", totalLines, strings.Join(parts, "\n")), nil
}

// ExtractFunctionSource returns the source code of a specific function.
func ExtractFunctionSource(filepath, functionName string) (string, error) {
	content, err := os.ReadFile(filepath)
	if err != nil {
		return "", err
	}

	lines := strings.Split(string(content), "\n")
	startLine := -1
	endLine := -1

	for i, line := range lines {
		trimmed := strings.TrimSpace(line)
		if m := reFuncDef.FindStringSubmatch(trimmed); m != nil && m[1] == functionName {
			startLine = i + 1
			indent := len(line) - len(strings.TrimLeft(line, " "))
			for j := i + 1; j < len(lines); j++ {
				if strings.TrimSpace(lines[j]) == "" {
					continue
				}
				lineIndent := len(lines[j]) - len(strings.TrimLeft(lines[j], " "))
				if lineIndent <= indent {
					endLine = j
					break
				}
			}
			if endLine == -1 {
				endLine = len(lines)
			}
			break
		}
	}

	if startLine == -1 {
		return fmt.Sprintf("未找到函数 '%s'。请检查函数名是否正确。", functionName), nil
	}

	var numbered []string
	for i := startLine - 1; i < endLine; i++ {
		numbered = append(numbered, fmt.Sprintf("  %d | %s", i+1, lines[i]))
	}

	return fmt.Sprintf("[函数 %s 源码] (line %d-%d)\n\n%s", functionName, startLine, endLine, strings.Join(numbered, "\n")), nil
}

// ExtractClassSource returns the source code of a specific class.
func ExtractClassSource(filepath, className string) (string, error) {
	content, err := os.ReadFile(filepath)
	if err != nil {
		return "", err
	}

	lines := strings.Split(string(content), "\n")
	startLine := -1
	endLine := -1

	for i, line := range lines {
		trimmed := strings.TrimSpace(line)
		if m := reClassDef.FindStringSubmatch(trimmed); m != nil && m[1] == className {
			startLine = i + 1
			indent := len(line) - len(strings.TrimLeft(line, " "))
			for j := i + 1; j < len(lines); j++ {
				if strings.TrimSpace(lines[j]) == "" {
					continue
				}
				lineIndent := len(lines[j]) - len(strings.TrimLeft(lines[j], " "))
				if lineIndent <= indent {
					endLine = j
					break
				}
			}
			if endLine == -1 {
				endLine = len(lines)
			}
			break
		}
	}

	if startLine == -1 {
		return fmt.Sprintf("未找到类 '%s'。请检查类名是否正确。", className), nil
	}

	var numbered []string
	for i := startLine - 1; i < endLine; i++ {
		numbered = append(numbered, fmt.Sprintf("  %d | %s", i+1, lines[i]))
	}

	return fmt.Sprintf("[类 %s 源码] (line %d-%d)\n\n%s", className, startLine, endLine, strings.Join(numbered, "\n")), nil
}
