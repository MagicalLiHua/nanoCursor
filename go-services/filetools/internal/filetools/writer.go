package filetools

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

// WriteFile creates a new file. Returns error if file already exists.
func WriteFile(workspace, filename, content string) (string, error) {
	safePath, err := GetSafeFilepath(workspace, filename)
	if err != nil {
		return "", err
	}

	if _, err := os.Stat(safePath); err == nil {
		return fmt.Sprintf("错误：文件 %s 已存在。write_file 仅用于创建新文件，请使用 edit_file 工具修改已有文件。", filename), nil
	}

	dir := filepath.Dir(safePath)
	if dir != "" {
		if err := os.MkdirAll(dir, 0755); err != nil {
			return "", err
		}
	}

	if err := os.WriteFile(safePath, []byte(content), 0644); err != nil {
		return "", err
	}

	return fmt.Sprintf("Successfully created file: %s", filename), nil
}

// EditFile edits a file using three-stage matching: exact -> stripped -> fuzzy.
func EditFile(workspace, filename, searchBlock, replaceBlock string) (string, error) {
	safePath, err := GetSafeFilepath(workspace, filename)
	if err != nil {
		return "", err
	}

	if _, err := os.Stat(safePath); os.IsNotExist(err) {
		return fmt.Sprintf("错误：文件 %s 不存在。请先使用 write_file 创建它。", filename), nil
	}

	content, err := os.ReadFile(safePath)
	if err != nil {
		return "", err
	}

	contentStr := string(content)
	var newContent string
	matchStrategy := ""
	occurrenceCount := 0
	startLine := 0
	endLine := 0

	// Strategy 1: Exact match
	if strings.Contains(contentStr, searchBlock) {
		occurrenceCount = strings.Count(contentStr, searchBlock)
		if occurrenceCount != 1 {
			return fmt.Sprintf("修改失败：%s 中 search_block 出现 %d 次，请提供更长且唯一的上下文。", filename, occurrenceCount), nil
		}
		idx := strings.Index(contentStr, searchBlock)
		startLine = strings.Count(contentStr[:idx], "\n") + 1
		endLine = startLine + strings.Count(searchBlock, "\n")
		newContent = strings.Replace(contentStr, searchBlock, replaceBlock, 1)
		matchStrategy = "精确匹配 (Exact Match)"

	} else if stripped := strings.TrimSpace(searchBlock); strings.Contains(contentStr, stripped) {
		// Strategy 2: Stripped match
		occurrenceCount = strings.Count(contentStr, stripped)
		if occurrenceCount != 1 {
			return fmt.Sprintf("修改失败：%s 中去空白后的 search_block 出现 %d 次。", filename, occurrenceCount), nil
		}
		idx := strings.Index(contentStr, stripped)
		startLine = strings.Count(contentStr[:idx], "\n") + 1
		endLine = startLine + strings.Count(stripped, "\n")
		newContent = strings.Replace(contentStr, stripped, strings.TrimSpace(replaceBlock), 1)
		matchStrategy = "首尾去空匹配 (Stripped Match)"

	} else {
		// Strategy 3: Fuzzy match
		sLine, eLine, ratio := FuzzySearchBlock(contentStr, searchBlock)
		if ratio < FuzzyMatchThreshold {
			return fmt.Sprintf("修改失败：未能在 %s 中找到指定的 search_block。最佳匹配相似度 %.1f%%，低于阈值 90%%。", filename, ratio*100), nil
		}

		contentLines := strings.Split(contentStr, "\n")
		beforeBlock := strings.Join(contentLines[:sLine-1], "\n")
		afterBlock := strings.Join(contentLines[eLine:], "\n")
		newContent = beforeBlock + "\n" + replaceBlock + "\n" + afterBlock
		startLine = sLine
		endLine = eLine
		occurrenceCount = 1
		matchStrategy = fmt.Sprintf("模糊匹配 (Fuzzy Match, 相似度 %.1f%%)", ratio*100)
	}

	if newContent == contentStr {
		return fmt.Sprintf("修改失败：%s 的替换结果没有产生任何内容变化。", filename), nil
	}

	backupPath := BackupFile(workspace, filename)

	if err := os.WriteFile(safePath, []byte(newContent), 0644); err != nil {
		return "", err
	}

	backupInfo := ""
	if backupPath != "" {
		backupInfo = fmt.Sprintf(" (原文件已备份到 %s)", filepath.Base(backupPath))
	}

	changedLines := strings.Count(newContent, "\n") - strings.Count(contentStr, "\n")
	if changedLines < 0 {
		changedLines = -changedLines
	}

	diffPreview := GenerateDiff(filename, contentStr, newContent, 80)

	return fmt.Sprintf(
		"成功修改 %s。使用策略: [%s]%s\nEdit Receipt:\n- path: %s\n- strategy: %s\n- matched_lines: %d-%d\n- occurrence_count: %d\n- changed_line_count: %d\n```diff\n%s\n```",
		filename, matchStrategy, backupInfo,
		filename, matchStrategy, startLine, endLine, occurrenceCount, changedLines, diffPreview,
	), nil
}
