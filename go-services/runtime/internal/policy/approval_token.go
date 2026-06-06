package policy

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"os"
	"strings"
	"time"
)

const approvalSecretEnv = "NANOCURSOR_RUNTIME_APPROVAL_SECRET"

type approvalPayload struct {
	ApprovalID      string `json:"approval_id"`
	Command         string `json:"command"`
	WorkspaceDir    string `json:"workspace_dir"`
	PermissionLevel string `json:"permission_level"`
	ExpiresAt       int64  `json:"expires_at"`
}

func ValidateApprovalToken(input Input, normalizedWorkspace string) string {
	token := strings.TrimSpace(input.ApprovalToken)
	if token == "" {
		return "approval token missing"
	}
	parts := strings.Split(token, ".")
	if len(parts) != 2 {
		return "approval token format is invalid"
	}
	signature, ok := signApprovalPayload(parts[0])
	if !ok {
		return "approval secret missing"
	}
	if !hmac.Equal([]byte(parts[1]), []byte(signature)) {
		return "approval token signature is invalid"
	}
	raw, err := base64.RawURLEncoding.DecodeString(parts[0])
	if err != nil {
		return "approval token payload is invalid"
	}
	var payload approvalPayload
	if err := json.Unmarshal(raw, &payload); err != nil {
		return "approval token payload is invalid"
	}
	if payload.ExpiresAt <= time.Now().Unix() {
		return "approval token expired"
	}
	if input.ApprovalID != "" && payload.ApprovalID != input.ApprovalID {
		return "approval token id mismatch"
	}
	if payload.Command != input.Command {
		return "approval token command mismatch"
	}
	if payload.WorkspaceDir != normalizedWorkspace {
		return "approval token workspace mismatch"
	}
	if payload.PermissionLevel != normalizedPermission(input) {
		return "approval token permission mismatch"
	}
	return ""
}

func signApprovalPayload(rawPayload string) (string, bool) {
	secret := approvalSecret()
	if secret == "" {
		return "", false
	}
	mac := hmac.New(sha256.New, []byte(secret))
	_, _ = mac.Write([]byte(rawPayload))
	return base64.RawURLEncoding.EncodeToString(mac.Sum(nil)), true
}

func approvalSecret() string {
	return strings.TrimSpace(os.Getenv(approvalSecretEnv))
}
