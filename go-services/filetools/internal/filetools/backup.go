package filetools

import (
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

// BackupFile backs up a file to .backups/ directory. Returns backup path or empty string.
func BackupFile(workspace, filename string) string {
	safePath, err := GetSafeFilepath(workspace, filename)
	if err != nil {
		return ""
	}

	if _, err := os.Stat(safePath); os.IsNotExist(err) {
		return ""
	}

	backupDir := filepath.Join(workspace, ".backups")
	os.MkdirAll(backupDir, 0755)

	safeName := strings.ReplaceAll(filename, string(filepath.Separator), "_")
	timestamp := time.Now().Format("20060102_150405.000000000")
	backupPath := filepath.Join(backupDir, fmt.Sprintf("%s.bak.%s", safeName, timestamp))

	if err := copyFile(safePath, backupPath); err != nil {
		return ""
	}

	return backupPath
}

// RollbackFile restores a file from backup.
func RollbackFile(workspace, filename string, backupIndex int) (string, error) {
	backupDir := filepath.Join(workspace, ".backups")
	safeName := strings.ReplaceAll(filename, string(filepath.Separator), "_")
	backupPrefix := safeName + ".bak."

	entries, err := os.ReadDir(backupDir)
	if err != nil {
		return fmt.Sprintf("未找到文件 %s 的备份。", filename), nil
	}

	var backups []string
	for _, entry := range entries {
		if strings.HasPrefix(entry.Name(), backupPrefix) {
			backups = append(backups, entry.Name())
		}
	}

	if len(backups) == 0 {
		return fmt.Sprintf("未找到文件 %s 的备份。", filename), nil
	}

	sort.Strings(backups)

	idx := backupIndex
	if idx < 0 {
		idx = len(backups) + idx
	}
	if idx < 0 || idx >= len(backups) {
		return fmt.Sprintf("备份索引 %d 超出范围（共 %d 个备份）。", backupIndex, len(backups)), nil
	}

	selected := backups[idx]
	backupPath := filepath.Join(backupDir, selected)

	safePath, err := GetSafeFilepath(workspace, filename)
	if err != nil {
		return "", err
	}

	os.MkdirAll(filepath.Dir(safePath), 0755)

	if err := copyFile(backupPath, safePath); err != nil {
		return fmt.Sprintf("回滚失败: %v", err), nil
	}

	return fmt.Sprintf("成功回滚文件 %s，使用备份: %s", filename, selected), nil
}

// ListBackups lists backup files, optionally filtered by filename.
func ListBackups(workspace, filename string) (string, error) {
	backupDir := filepath.Join(workspace, ".backups")

	entries, err := os.ReadDir(backupDir)
	if err != nil {
		return "没有备份文件。", nil
	}

	var backups []string
	for _, entry := range entries {
		name := entry.Name()
		if filename != "" {
			safeName := strings.ReplaceAll(filename, string(filepath.Separator), "_")
			if !strings.HasPrefix(name, safeName) {
				continue
			}
		}
		backups = append(backups, name)
	}

	if len(backups) == 0 {
		return "没有备份文件。", nil
	}

	sort.Strings(backups)

	var result strings.Builder
	result.WriteString(fmt.Sprintf("找到的 %d 个备份:\n", len(backups)))
	for i, name := range backups {
		info, _ := os.Stat(filepath.Join(backupDir, name))
		size := int64(0)
		if info != nil {
			size = info.Size()
		}
		result.WriteString(fmt.Sprintf("  %d: %s (%d bytes)\n", i, name, size))
	}

	return result.String(), nil
}

func copyFile(src, dst string) error {
	data, err := os.ReadFile(src)
	if err != nil {
		return err
	}
	return os.WriteFile(dst, data, 0644)
}
