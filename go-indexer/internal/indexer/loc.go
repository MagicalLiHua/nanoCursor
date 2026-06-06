package indexer

import (
	"bufio"
	"os"
	"strings"
)

// countLOC counts non-blank, non-comment lines of code.
func countLOC(path string, language string) int {
	f, err := os.Open(path)
	if err != nil {
		return 0
	}
	defer f.Close()

	count := 0
	scanner := bufio.NewScanner(f)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" {
			continue
		}
		if language == "python" && strings.HasPrefix(line, "#") {
			continue
		}
		if (language == "javascript" || language == "typescript") && strings.HasPrefix(line, "//") {
			continue
		}
		count++
	}
	return count
}
