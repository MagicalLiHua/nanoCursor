package mcp

type Preset struct {
	ID             string   `json:"id"`
	ServerID       string   `json:"server_id"`
	Name           string   `json:"name"`
	Description    string   `json:"description"`
	Command        string   `json:"command"`
	Args           []string `json:"args"`
	EnvKeys        []string `json:"env_keys"`
	EnabledDefault bool     `json:"enabled_default"`
	Requires       []string `json:"requires"`
	SecurityNote   string   `json:"security_note"`
}

type ProbeRequest struct {
	ServerID     string            `json:"server_id"`
	WorkspaceDir string            `json:"workspace_dir"`
	Command      string            `json:"command"`
	Args         []string          `json:"args"`
	EnvKeys      []string          `json:"env_keys"`
	Env          map[string]string `json:"env"`
	Enabled      *bool             `json:"enabled,omitempty"`
}

type ProbeCheck struct {
	ID      string `json:"id"`
	Status  string `json:"status"`
	Message string `json:"message"`
}

type ProbeResult struct {
	ServerID string       `json:"server_id"`
	Status   string       `json:"status"`
	Ok       bool         `json:"ok"`
	Checks   []ProbeCheck `json:"checks"`
	Command  string       `json:"command"`
	Args     []string     `json:"args"`
}

type ToolCatalog struct {
	ServerID string     `json:"server_id"`
	Status   string     `json:"status"`
	Ok       bool       `json:"ok"`
	Tools    []ToolInfo `json:"tools"`
	Error    string     `json:"error,omitempty"`
}

type CallRequest struct {
	ServerID     string         `json:"server_id"`
	RunID        string         `json:"run_id,omitempty"`
	ToolName     string         `json:"tool_name"`
	Arguments    map[string]any `json:"arguments"`
	WorkspaceDir string         `json:"workspace_dir,omitempty"`
	Policy       struct {
		PermissionLevel  string `json:"permission_level,omitempty"`
		RequiresApproval bool   `json:"requires_approval,omitempty"`
		ApprovalID       string `json:"approval_id,omitempty"`
		ApprovalToken    string `json:"approval_token,omitempty"`
	} `json:"policy"`
}

type ToolInfo struct {
	Name             string `json:"name"`
	Description      string `json:"description"`
	PermissionLevel  string `json:"permission_level"`
	RequiresApproval bool   `json:"requires_approval"`
}
