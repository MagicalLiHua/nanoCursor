package filetools

import (
	"fmt"
	"os"
	"sort"
	"strings"
)

// ReadFile reads a file. Small files return full content, large files return AST outline.
func ReadFile(workspace, filename string) (string, error) {
	safePath, err := GetSafeFilepath(workspace, filename)
	if err != nil {
		return "", err
	}

	info, err := os.Stat(safePath)
	if os.IsNotExist(err) {
		return fmt.Sprintf("Error: File '%s' does not exist. Cannot read.", filename), nil
	}
	if err != nil {
		return "", err
	}

	content, err := os.ReadFile(safePath)
	if err != nil {
		return "", err
	}

	if len(content) <= LargeFileThreshold {
		return fmt.Sprintf("--- Content of %s ---\n%s\n--- End of %s ---", filename, string(content), filename), nil
	}

	outline, err := ExtractOutline(safePath)
	if err != nil {
		outline = fmt.Sprintf("(AST 解析失败: %v)", err)
	}
	return fmt.Sprintf("--- Structure of %s (%d bytes, 大文件) ---\n%s\n--- End of %s ---", filename, info.Size(), outline, filename), nil
}

// ReadFunction extracts a function's source code using AST.
func ReadFunction(workspace, filename, functionName string) (string, error) {
	safePath, err := GetSafeFilepath(workspace, filename)
	if err != nil {
		return "", err
	}

	if _, err := os.Stat(safePath); os.IsNotExist(err) {
		return fmt.Sprintf("Error: File '%s' does not exist.", filename), nil
	}

	return ExtractFunctionSource(safePath, functionName)
}

// ReadClass extracts a class's source code using AST.
func ReadClass(workspace, filename, className string) (string, error) {
	safePath, err := GetSafeFilepath(workspace, filename)
	if err != nil {
		return "", err
	}

	if _, err := os.Stat(safePath); os.IsNotExist(err) {
		return fmt.Sprintf("Error: File '%s' does not exist.", filename), nil
	}

	return ExtractClassSource(safePath, className)
}

// ReadFileRange reads a specific line range from a file.
func ReadFileRange(workspace, filename string, startLine, endLine int) (string, error) {
	safePath, err := GetSafeFilepath(workspace, filename)
	if err != nil {
		return "", err
	}

	if _, err := os.Stat(safePath); os.IsNotExist(err) {
		return fmt.Sprintf("Error: File '%s' does not exist.", filename), nil
	}

	content, err := os.ReadFile(safePath)
	if err != nil {
		return "", err
	}

	lines := strings.Split(string(content), "\n")
	totalLines := len(lines)

	if startLine < 1 {
		return fmt.Sprintf("Error: start_line 必须 >= 1，当前值: %d", startLine), nil
	}
	if endLine > totalLines {
		return fmt.Sprintf("Error: end_line 超出范围。文件共 %d 行，请求: %d", totalLines, endLine), nil
	}
	if startLine > endLine {
		return fmt.Sprintf("Error: start_line (%d) 不能大于 end_line (%d)", startLine, endLine), nil
	}

	selected := lines[startLine-1 : endLine]
	var numbered []string
	for i, line := range selected {
		numbered = append(numbered, fmt.Sprintf("  %d | %s", i+startLine, line))
	}

	return fmt.Sprintf("--- Lines %d-%d of %s ---\n%s\n--- End ---", startLine, endLine, filename, strings.Join(numbered, "\n")), nil
}

// ListDirectory lists files and subdirectories in a path.
func ListDirectory(workspace, path string) (string, error) {
	safePath, err := GetSafeFilepath(workspace, path)
	if err != nil {
		return "", err
	}

	info, err := os.Stat(safePath)
	if os.IsNotExist(err) || !info.IsDir() {
		return fmt.Sprintf("Error: '%s' 不是一个存在的目录。", path), nil
	}

	entries, err := os.ReadDir(safePath)
	if err != nil {
		return "", err
	}

	sort.Slice(entries, func(i, j int) bool {
		return entries[i].Name() < entries[j].Name()
	})

	var lines []string
	for _, entry := range entries {
		name := entry.Name()
		if strings.HasPrefix(name, ".backups") || strings.HasPrefix(name, ".snapshots") {
			continue
		}
		if entry.IsDir() {
			lines = append(lines, fmt.Sprintf("  [DIR]  %s", name))
		} else {
			lines = append(lines, fmt.Sprintf("  [FILE] %s", name))
		}
	}

	if len(lines) == 0 {
		return fmt.Sprintf("目录 '%s' 为空。", path), nil
	}

	return fmt.Sprintf("目录 '%s' 的内容:\n%s", path, strings.Join(lines, "\n")), nil
}
