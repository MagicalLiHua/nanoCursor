package filetools

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

type WriteOptions struct {
	Overwrite      bool
	BackupExisting bool
}

type EditOptions struct {
	SearchBlock  string
	ReplaceBlock string
	StartLine    int
	EndLine      int
	NewText      string
	MatchMode    string
	CreateBackup bool
}

type EditResult struct {
	Result           string
	Diff             string
	Strategy         string
	MatchedStartLine int
	MatchedEndLine   int
	ChangedLineCount int
	BackupPath       string
	Changed          bool
}

// WriteFile creates a new file. Returns error if file already exists.
func WriteFile(workspace, filename, content string) (string, error) {
	return WriteFileWithOptions(workspace, filename, content, WriteOptions{})
}

// WriteFileWithOptions writes a file and can optionally overwrite existing content.
func WriteFileWithOptions(workspace, filename, content string, opts WriteOptions) (string, error) {
	safePath, err := GetSafeFilepath(workspace, filename)
	if err != nil {
		return "", err
	}

	existed := false
	if _, err := os.Stat(safePath); err == nil {
		existed = true
	}
	if existed && !opts.Overwrite {
		return fmt.Sprintf("错误：文件 %s 已存在。write_file 仅用于创建新文件，请使用 edit_file 工具修改已有文件。", filename), nil
	}

	dir := filepath.Dir(safePath)
	if dir != "" {
		if err := os.MkdirAll(dir, 0755); err != nil {
			return "", err
		}
	}

	backupPath := ""
	if existed && opts.BackupExisting {
		backupPath = BackupFile(workspace, filename)
	}

	if err := os.WriteFile(safePath, []byte(content), 0644); err != nil {
		return "", err
	}

	action := "created"
	if existed {
		action = "updated"
	}
	backupInfo := ""
	if backupPath != "" {
		backupInfo = fmt.Sprintf(" (backup: %s)", filepath.Base(backupPath))
	}
	return fmt.Sprintf("Successfully %s file: %s%s", action, filename, backupInfo), nil
}

// EditFile edits a file using three-stage matching: exact -> stripped -> fuzzy.
func EditFile(workspace, filename, searchBlock, replaceBlock string) (string, error) {
	result, err := EditFileWithOptions(workspace, filename, EditOptions{
		SearchBlock:  searchBlock,
		ReplaceBlock: replaceBlock,
		MatchMode:    "fuzzy",
		CreateBackup: true,
	})
	if err != nil {
		return "", err
	}
	return result.Result, nil
}

// EditFileWithOptions edits a file by line range or search/replace.
func EditFileWithOptions(workspace, filename string, opts EditOptions) (EditResult, error) {
	safePath, err := GetSafeFilepath(workspace, filename)
	if err != nil {
		return EditResult{}, err
	}

	if _, err := os.Stat(safePath); os.IsNotExist(err) {
		return EditResult{Result: fmt.Sprintf("错误：文件 %s 不存在。请先使用 write_file 创建它。", filename)}, nil
	}

	content, err := os.ReadFile(safePath)
	if err != nil {
		return EditResult{}, err
	}

	contentStr := string(content)
	var newContent string
	matchStrategy := ""
	occurrenceCount := 0
	startLine := 0
	endLine := 0

	if opts.StartLine > 0 || opts.EndLine > 0 {
		lines := splitLinesKeepEnds(contentStr)
		if opts.StartLine < 1 || opts.EndLine > len(lines) || opts.StartLine > opts.EndLine {
			return EditResult{
				Result: fmt.Sprintf("修改失败：无效行号范围 %d-%d，文件共 %d 行。", opts.StartLine, opts.EndLine, len(lines)),
			}, nil
		}
		replacementLines := splitReplacementLines(opts.NewText)
		newContent = strings.Join(lines[:opts.StartLine-1], "") +
			strings.Join(replacementLines, "") +
			strings.Join(lines[opts.EndLine:], "")
		startLine = opts.StartLine
		endLine = opts.EndLine
		occurrenceCount = 1
		matchStrategy = "行号范围匹配 (Line Range)"
	} else if opts.SearchBlock != "" {
		searchBlock := opts.SearchBlock
		replaceBlock := opts.ReplaceBlock
		matchMode := strings.ToLower(strings.TrimSpace(opts.MatchMode))
		matched := false
		if matchMode == "" {
			matchMode = "fuzzy"
		}

		// Strategy 1: Exact match
		if strings.Contains(contentStr, searchBlock) {
			occurrenceCount = strings.Count(contentStr, searchBlock)
			if occurrenceCount != 1 {
				return EditResult{Result: fmt.Sprintf("修改失败：%s 中 search_block 出现 %d 次，请提供更长且唯一的上下文。", filename, occurrenceCount)}, nil
			}
			idx := strings.Index(contentStr, searchBlock)
			startLine = strings.Count(contentStr[:idx], "\n") + 1
			endLine = startLine + strings.Count(searchBlock, "\n")
			newContent = strings.Replace(contentStr, searchBlock, replaceBlock, 1)
			matchStrategy = "精确匹配 (Exact Match)"
			matched = true

		} else if matchMode != "exact" {
			stripped := strings.TrimSpace(searchBlock)
			if stripped != "" && strings.Contains(contentStr, stripped) {
				// Strategy 2: Stripped match
				occurrenceCount = strings.Count(contentStr, stripped)
				if occurrenceCount != 1 {
					return EditResult{Result: fmt.Sprintf("修改失败：%s 中去空白后的 search_block 出现 %d 次。", filename, occurrenceCount)}, nil
				}
				idx := strings.Index(contentStr, stripped)
				startLine = strings.Count(contentStr[:idx], "\n") + 1
				endLine = startLine + strings.Count(stripped, "\n")
				newContent = strings.Replace(contentStr, stripped, strings.TrimSpace(replaceBlock), 1)
				matchStrategy = "首尾去空匹配 (Stripped Match)"
				matched = true
			} else if matchMode == "fuzzy" {
				// Strategy 3: Fuzzy match
				sLine, eLine, ratio := FuzzySearchBlock(contentStr, searchBlock)
				if ratio < FuzzyMatchThreshold {
					return EditResult{Result: fmt.Sprintf("修改失败：未能在 %s 中找到指定的 search_block。最佳匹配相似度 %.1f%%，低于阈值 90%%。", filename, ratio*100)}, nil
				}

				contentLines := strings.Split(contentStr, "\n")
				beforeBlock := strings.Join(contentLines[:sLine-1], "\n")
				afterBlock := strings.Join(contentLines[eLine:], "\n")
				newContent = beforeBlock + "\n" + replaceBlock + "\n" + afterBlock
				startLine = sLine
				endLine = eLine
				occurrenceCount = 1
				matchStrategy = fmt.Sprintf("模糊匹配 (Fuzzy Match, 相似度 %.1f%%)", ratio*100)
				matched = true
			}
		}

		if !matched {
			return EditResult{Result: fmt.Sprintf("修改失败：未能在 %s 中找到指定的 search_block。", filename)}, nil
		}
	} else {
		return EditResult{Result: "修改失败：请提供 start_line/end_line/new_text 或 search_block/replace_block。"}, nil
	}

	if newContent == contentStr {
		return EditResult{Result: fmt.Sprintf("修改失败：%s 的替换结果没有产生任何内容变化。", filename)}, nil
	}

	backupPath := ""
	if opts.CreateBackup {
		backupPath = BackupFile(workspace, filename)
	}

	if err := os.WriteFile(safePath, []byte(newContent), 0644); err != nil {
		return EditResult{}, err
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
	resultText := fmt.Sprintf(
		"成功修改 %s。使用策略: [%s]%s\nEdit Receipt:\n- path: %s\n- strategy: %s\n- matched_lines: %d-%d\n- occurrence_count: %d\n- changed_line_count: %d\n```diff\n%s\n```",
		filename, matchStrategy, backupInfo,
		filename, matchStrategy, startLine, endLine, occurrenceCount, changedLines, diffPreview,
	)

	return EditResult{
		Result:           resultText,
		Diff:             diffPreview,
		Strategy:         matchStrategy,
		MatchedStartLine: startLine,
		MatchedEndLine:   endLine,
		ChangedLineCount: changedLines,
		BackupPath:       backupPath,
		Changed:          true,
	}, nil
}

func splitLinesKeepEnds(content string) []string {
	if content == "" {
		return []string{}
	}
	lines := strings.SplitAfter(content, "\n")
	if lines[len(lines)-1] == "" {
		return lines[:len(lines)-1]
	}
	return lines
}

func splitReplacementLines(content string) []string {
	if content == "" {
		return []string{}
	}
	lines := splitLinesKeepEnds(content)
	if len(lines) > 0 && !strings.HasSuffix(lines[len(lines)-1], "\n") {
		lines[len(lines)-1] += "\n"
	}
	return lines
}
