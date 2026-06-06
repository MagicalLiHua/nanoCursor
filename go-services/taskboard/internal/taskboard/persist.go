package taskboard

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"time"
)

// SaveBoard persists the board to run_state.json with atomic write.
func SaveBoard(board *RunTaskBoard, runDir string) (string, error) {
	path := filepath.Join(runDir, "run_state.json")
	tmp := filepath.Join(runDir, fmt.Sprintf(".run_state.%08x.tmp", time.Now().UnixNano()^int64(os.Getpid())))

	data, err := json.MarshalIndent(board, "", "  ")
	if err != nil {
		return "", fmt.Errorf("marshal board: %w", err)
	}
	if err := os.MkdirAll(runDir, 0755); err != nil {
		return "", fmt.Errorf("mkdir: %w", err)
	}
	if err := os.WriteFile(tmp, data, 0644); err != nil {
		return "", fmt.Errorf("write tmp: %w", err)
	}
	if err := os.Rename(tmp, path); err != nil {
		os.Remove(tmp)
		return "", fmt.Errorf("rename: %w", err)
	}

	// Legacy artifact
	legacyPath := filepath.Join(runDir, "run_graph.json")
	legacyTmp := filepath.Join(runDir, fmt.Sprintf(".run_graph.%08x.tmp", time.Now().UnixNano()^int64(os.Getpid())))
	if err := os.WriteFile(legacyTmp, data, 0644); err == nil {
		os.Rename(legacyTmp, legacyPath)
	}

	return path, nil
}

// LoadBoard loads a board from run_state.json (fallback to run_graph.json).
func LoadBoard(runDir string) (*RunTaskBoard, error) {
	path := filepath.Join(runDir, "run_state.json")
	if _, err := os.Stat(path); os.IsNotExist(err) {
		path = filepath.Join(runDir, "run_graph.json")
	}
	if _, err := os.Stat(path); os.IsNotExist(err) {
		return nil, nil
	}

	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read file: %w", err)
	}
	var board RunTaskBoard
	if err := json.Unmarshal(data, &board); err != nil {
		return nil, fmt.Errorf("unmarshal: %w", err)
	}
	return &board, nil
}
