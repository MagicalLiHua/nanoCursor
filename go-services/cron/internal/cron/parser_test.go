package cron

import (
	"testing"
	"time"
)

func TestCronMatchesWildcard(t *testing.T) {
	if !CronMatches("* * * * *", time.Now()) {
		t.Fatal("wildcard should match any time")
	}
}

func TestCronMatchesExactMinute(t *testing.T) {
	dt := time.Date(2026, 6, 6, 14, 30, 0, 0, time.UTC)
	if !CronMatches("30 * * * *", dt) {
		t.Fatal("should match minute 30")
	}
	if CronMatches("31 * * * *", dt) {
		t.Fatal("should not match minute 31")
	}
}

func TestCronMatchesStep(t *testing.T) {
	dt := time.Date(2026, 6, 6, 14, 10, 0, 0, time.UTC)
	if !CronMatches("*/5 * * * *", dt) {
		t.Fatal("*/5 should match minute 10")
	}
	if CronMatches("*/5 * * * *", dt.Add(time.Minute)) {
		t.Fatal("*/5 should not match minute 11")
	}
}

func TestCronMatchesRange(t *testing.T) {
	dt := time.Date(2026, 6, 6, 14, 25, 0, 0, time.UTC)
	if !CronMatches("25-35 * * * *", dt) {
		t.Fatal("25-35 should match 25")
	}
	if CronMatches("25-35 * * * *", dt.Add(11*time.Minute)) {
		t.Fatal("25-35 should not match 36")
	}
}

func TestCronMatchesCommaList(t *testing.T) {
	dt := time.Date(2026, 6, 6, 14, 15, 0, 0, time.UTC)
	if !CronMatches("15,30,45 * * * *", dt) {
		t.Fatal("15,30,45 should match 15")
	}
	if CronMatches("15,30,45 * * * *", dt.Add(time.Minute)) {
		t.Fatal("15,30,45 should not match 16")
	}
}

func TestCronMatchesHourAndMinute(t *testing.T) {
	dt := time.Date(2026, 6, 6, 9, 0, 0, 0, time.UTC)
	if !CronMatches("0 9 * * *", dt) {
		t.Fatal("0 9 should match 9:00")
	}
	if CronMatches("0 10 * * *", dt) {
		t.Fatal("0 10 should not match 9:00")
	}
}

func TestCronMatchesDayOfWeek(t *testing.T) {
	// 2026-06-06 is a Saturday (weekday 6)
	dt := time.Date(2026, 6, 6, 12, 0, 0, 0, time.UTC)
	if !CronMatches("0 12 * * 6", dt) {
		t.Fatal("should match Saturday")
	}
	if CronMatches("0 12 * * 0", dt) {
		t.Fatal("should not match Sunday")
	}
}

func TestCronMatchesInvalidExpr(t *testing.T) {
	if CronMatches("* * *", time.Now()) {
		t.Fatal("3 fields should be invalid")
	}
	if CronMatches("* * * * * *", time.Now()) {
		t.Fatal("6 fields should be invalid")
	}
}
