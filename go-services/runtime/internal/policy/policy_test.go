package policy

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"strconv"
	"testing"
	"time"
)

const testApprovalSecret = "nanocursor-test-approval-secret"

func TestPreviewAllowsSafeCommandInsideWorkspace(t *testing.T) {
	decision := Preview(Input{
		WorkspaceDir:    t.TempDir(),
		Command:         "python -m pytest -q",
		PermissionLevel: "shell_safe",
	})
	if !decision.Allowed {
		t.Fatalf("expected allowed decision, got %#v", decision)
	}
	if decision.PermissionLevel != "shell_safe" {
		t.Fatalf("expected shell_safe, got %s", decision.PermissionLevel)
	}
}

func TestPreviewRejectsRiskyCommandWithoutApproval(t *testing.T) {
	decision := Preview(Input{
		WorkspaceDir: t.TempDir(),
		Command:      "rm -rf dist",
	})
	if decision.Allowed {
		t.Fatalf("expected denial, got %#v", decision)
	}
	if decision.ErrorCode != "approval_required" {
		t.Fatalf("expected approval_required, got %s", decision.ErrorCode)
	}
}

func TestPreviewAllowsRiskyCommandWithValidApprovalToken(t *testing.T) {
	workspace := t.TempDir()
	t.Setenv(approvalSecretEnv, testApprovalSecret)
	token := testApprovalToken(`{"approval_id":"approval_123","command":"rm -rf dist","expires_at":` +
		strconv.FormatInt(time.Now().Add(time.Minute).Unix(), 10) +
		`,"permission_level":"shell_risky","workspace_dir":"` + workspace + `"}`)
	decision := Preview(Input{
		WorkspaceDir:     workspace,
		Command:          "rm -rf dist",
		PermissionLevel:  "shell_risky",
		RequiresApproval: true,
		ApprovalID:       "approval_123",
		ApprovalToken:    token,
	})
	if !decision.Allowed {
		t.Fatalf("expected valid token to allow command, got %#v", decision)
	}
}

func TestPreviewRejectsRiskyCommandWithMismatchedApprovalToken(t *testing.T) {
	workspace := t.TempDir()
	t.Setenv(approvalSecretEnv, testApprovalSecret)
	token := testApprovalToken(`{"approval_id":"approval_123","command":"rm -rf other","expires_at":` +
		strconv.FormatInt(time.Now().Add(time.Minute).Unix(), 10) +
		`,"permission_level":"shell_risky","workspace_dir":"` + workspace + `"}`)
	decision := Preview(Input{
		WorkspaceDir:     workspace,
		Command:          "rm -rf dist",
		PermissionLevel:  "shell_risky",
		RequiresApproval: true,
		ApprovalID:       "approval_123",
		ApprovalToken:    token,
	})
	if decision.Allowed {
		t.Fatalf("expected mismatched token denial, got %#v", decision)
	}
	if decision.ErrorCode != "approval_invalid" {
		t.Fatalf("expected approval_invalid, got %s", decision.ErrorCode)
	}
}

func TestPreviewRejectsApprovalTokenWhenSecretMissing(t *testing.T) {
	workspace := t.TempDir()
	token := testApprovalTokenWithSecret(`{"approval_id":"approval_123","command":"rm -rf dist","expires_at":`+
		strconv.FormatInt(time.Now().Add(time.Minute).Unix(), 10)+
		`,"permission_level":"shell_risky","workspace_dir":"`+workspace+`"}`, testApprovalSecret)
	decision := Preview(Input{
		WorkspaceDir:     workspace,
		Command:          "rm -rf dist",
		PermissionLevel:  "shell_risky",
		RequiresApproval: true,
		ApprovalID:       "approval_123",
		ApprovalToken:    token,
	})
	if decision.Allowed {
		t.Fatalf("expected missing secret denial, got %#v", decision)
	}
	if decision.ErrorCode != "approval_invalid" {
		t.Fatalf("expected approval_invalid, got %s", decision.ErrorCode)
	}
	if decision.Message != "approval secret missing" {
		t.Fatalf("expected missing secret message, got %s", decision.Message)
	}
}

func TestPreviewRejectsOutsideWorkspace(t *testing.T) {
	workspace := t.TempDir()
	outside := t.TempDir()
	decision := Preview(Input{
		WorkspaceDir: workspace,
		Cwd:          outside,
		Command:      "echo hi",
	})
	if decision.Allowed {
		t.Fatalf("expected workspace boundary denial, got %#v", decision)
	}
	if decision.ErrorCode != "workspace_boundary_violation" {
		t.Fatalf("expected workspace_boundary_violation, got %s", decision.ErrorCode)
	}
}

func testApprovalToken(payload string) string {
	return testApprovalTokenWithSecret(payload, testApprovalSecret)
}

func testApprovalTokenWithSecret(payload string, secret string) string {
	rawPayload := base64.RawURLEncoding.EncodeToString([]byte(payload))
	mac := hmac.New(sha256.New, []byte(secret))
	_, _ = mac.Write([]byte(rawPayload))
	signature := base64.RawURLEncoding.EncodeToString(mac.Sum(nil))
	return rawPayload + "." + signature
}
