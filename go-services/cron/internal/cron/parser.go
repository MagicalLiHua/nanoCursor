package cron

import (
	"strconv"
	"strings"
	"time"
)

// CronMatches checks if a 5-field cron expression matches the given time.
func CronMatches(expr string, dt time.Time) bool {
	parts := strings.Fields(expr)
	if len(parts) != 5 {
		return false
	}
	return fieldMatches(parts[0], dt.Minute(), 0, 59) &&
		fieldMatches(parts[1], dt.Hour(), 0, 23) &&
		fieldMatches(parts[2], dt.Day(), 1, 31) &&
		fieldMatches(parts[3], int(dt.Month()), 1, 12) &&
		fieldMatches(parts[4], int(dt.Weekday()), 0, 6)
}

func fieldMatches(field string, value, lo, hi int) bool {
	if field == "*" {
		return true
	}
	if strings.Contains(field, "/") {
		baseStr, stepStr, ok := strings.Cut(field, "/")
		if !ok {
			return false
		}
		base := 0
		if baseStr != "*" {
			base, _ = strconv.Atoi(baseStr)
		}
		step, _ := strconv.Atoi(stepStr)
		if step <= 0 {
			return false
		}
		return (value-base)%step == 0
	}
	if strings.Contains(field, "-") {
		startStr, endStr, ok := strings.Cut(field, "-")
		if !ok {
			return false
		}
		start, _ := strconv.Atoi(startStr)
		end, _ := strconv.Atoi(endStr)
		return value >= start && value <= end
	}
	if strings.Contains(field, ",") {
		for _, part := range strings.Split(field, ",") {
			v, _ := strconv.Atoi(strings.TrimSpace(part))
			if v == value {
				return true
			}
		}
		return false
	}
	v, _ := strconv.Atoi(field)
	return v == value
}
