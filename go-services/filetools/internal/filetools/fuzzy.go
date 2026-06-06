package filetools

import (
	"fmt"
	"strings"
)

// SequenceRatio computes similarity between two strings (0.0 to 1.0).
func SequenceRatio(a, b string) float64 {
	if a == b {
		return 1.0
	}
	if len(a) == 0 || len(b) == 0 {
		return 0.0
	}

	aLines := strings.Split(a, "\n")
	bLines := strings.Split(b, "\n")

	lcs := lcsLength(aLines, bLines)
	return 2.0 * float64(lcs) / float64(len(aLines)+len(bLines))
}

func lcsLength(a, b []string) int {
	m, n := len(a), len(b)
	if m == 0 || n == 0 {
		return 0
	}

	prev := make([]int, n+1)
	curr := make([]int, n+1)

	for i := 1; i <= m; i++ {
		for j := 1; j <= n; j++ {
			if a[i-1] == b[j-1] {
				curr[j] = prev[j-1] + 1
			} else if prev[j] > curr[j-1] {
				curr[j] = prev[j]
			} else {
				curr[j] = curr[j-1]
			}
		}
		prev, curr = curr, prev
		for k := range curr {
			curr[k] = 0
		}
	}

	return prev[n]
}

// GenerateDiff creates a unified diff between two strings.
func GenerateDiff(filename, before, after string, maxLines int) string {
	beforeLines := strings.Split(before, "\n")
	afterLines := strings.Split(after, "\n")

	var diff []string
	diff = append(diff, fmt.Sprintf("--- a/%s", filename))
	diff = append(diff, fmt.Sprintf("+++ b/%s", filename))

	maxLen := len(beforeLines)
	if len(afterLines) > maxLen {
		maxLen = len(afterLines)
	}

	for i := 0; i < maxLen; i++ {
		bLine := ""
		aLine := ""
		if i < len(beforeLines) {
			bLine = beforeLines[i]
		}
		if i < len(afterLines) {
			aLine = afterLines[i]
		}

		if bLine != aLine {
			if bLine != "" {
				diff = append(diff, fmt.Sprintf("-%s", bLine))
			}
			if aLine != "" {
				diff = append(diff, fmt.Sprintf("+%s", aLine))
			}
		}
	}

	if len(diff) > maxLines {
		diff = diff[:maxLines]
		diff = append(diff, "... diff truncated")
	}

	return strings.Join(diff, "\n")
}

// FuzzySearchBlock searches for the best matching position of searchBlock in content.
// Returns (startLine, endLine, ratio) — 1-based inclusive line numbers.
func FuzzySearchBlock(content, searchBlock string) (int, int, float64) {
	contentLines := strings.Split(content, "\n")
	searchLines := strings.Split(searchBlock, "\n")

	if len(contentLines) > MaxFuzzyMatchLines {
		return 0, 0, 0
	}

	searchLen := len(searchLines)
	if searchLen == 0 {
		return 0, 0, 0
	}

	bestRatio := 0.0
	bestStart := 0

	for i := 0; i <= len(contentLines)-searchLen; i++ {
		window := strings.Join(contentLines[i:i+searchLen], "\n")
		search := strings.Join(searchLines, "\n")
		ratio := SequenceRatio(window, search)
		if ratio > bestRatio {
			bestRatio = ratio
			bestStart = i
		}
	}

	return bestStart + 1, bestStart + searchLen, bestRatio
}
