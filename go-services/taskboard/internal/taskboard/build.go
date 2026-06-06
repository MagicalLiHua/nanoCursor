package taskboard

import (
	"encoding/json"
	"fmt"
	"strings"
	"time"
)

// BuildBoard creates a task board from an execution plan JSON.
func BuildBoard(runID, planJSON, conversationID string) (*RunTaskBoard, error) {
	var plan map[string]interface{}
	if planJSON != "" {
		if err := json.Unmarshal([]byte(planJSON), &plan); err != nil {
			return nil, fmt.Errorf("invalid execution_plan JSON: %w", err)
		}
	}

	strategy := "feature_delivery"
	if s, ok := plan["strategy"].(string); ok && s != "" {
		strategy = s
	}
	now := float64(time.Now().UnixNano()) / 1e9

	if strategy == "lead_direct_reply" {
		return &RunTaskBoard{
			RunID: runID, ConversationID: conversationID, Strategy: strategy,
			Status: BoardCreated, Revision: 1,
			Metadata: map[string]interface{}{
				"runtime_model": "agent_loop_with_mutable_task_board",
				"graph_compat": true, "task_board_suppressed": true,
				"suppressed_reason": "lead_direct_reply",
			},
			CreatedAt: now, UpdatedAt: now,
		}, nil
	}

	nodes := []*RunTask{
		{
			ID: "node-001-intake", Type: "intake", Title: "接收需求",
			Goal: "确认任务目标、工作区边界和用户约束。",
			AgentRole: "lead", Status: StatusReady,
			Acceptance: []AcceptanceCriterion{{ID: "scope_confirmed", Description: "任务范围已确认。"}},
		},
		{
			ID: "node-002-context", Type: "context_build", Title: "构建上下文",
			Goal: "选择相关文件、摘要、最近变更和运行约束。",
			AgentRole: "lead", Dependencies: []string{"node-001-intake"},
			Acceptance: []AcceptanceCriterion{{ID: "context_ready", Description: "上下文包已生成。"}},
		},
	}

	stages, _ := plan["stages"].([]interface{})
	var analysisNodes, writeNodes, verifyNodes, reviewNodes []*RunTask

	for i, stage := range stages {
		stageMap, ok := stage.(map[string]interface{})
		if !ok {
			continue
		}
		stageID := fmt.Sprintf("stage-%d", i+1)
		if sid, ok := stageMap["id"].(string); ok && sid != "" {
			stageID = sid
		}
		if stageID == "intake" {
			continue
		}

		role := "agent"
		if r, ok := stageMap["owner_role"].(string); ok && r != "" {
			role = r
		} else if r, ok := stageMap["owner"].(string); ok && r != "" {
			role = r
		}
		title := stageID
		if t, ok := stageMap["title"].(string); ok && t != "" {
			title = t
		}
		desc := ""
		if d, ok := stageMap["description"].(string); ok {
			desc = d
		}

		nodeType := nodeTypeForStage(stageID, role, title)
		nodeID := fmt.Sprintf("node-%03d-%s", i+3, safeSlug(stageID))

		node := &RunTask{
			ID: nodeID, Type: nodeType, Title: title, Goal: desc,
			AgentRole: role, Status: StatusPending,
			CanParallel: nodeType == "analysis",
			WritesFiles: nodeType == "implementation",
			Acceptance:  []AcceptanceCriterion{{ID: "stage_done", Description: desc}},
		}
		if nodeType == "implementation" {
			node.ResourceLocks = []string{"global:workspace_write"}
		}

		switch nodeType {
		case "analysis", "plan":
			node.Dependencies = []string{"node-002-context"}
			analysisNodes = append(analysisNodes, node)
		case "implementation":
			writeNodes = append(writeNodes, node)
		case "test":
			verifyNodes = append(verifyNodes, node)
		case "review", "security":
			reviewNodes = append(reviewNodes, node)
		default:
			analysisNodes = append(analysisNodes, node)
		}
		nodes = append(nodes, node)
	}

	// Ensure at least one implementation node
	if len(writeNodes) == 0 && strategy != "analysis_only" && strategy != "docs_only" {
		nodeID := fmt.Sprintf("node-%03d-implementation", len(nodes)+1)
		node := &RunTask{
			ID: nodeID, Type: "implementation", Title: "代码实现",
			Goal: "按计划完成必要文件修改。", AgentRole: "coder",
			Status: StatusPending, WritesFiles: true,
			ResourceLocks: []string{"global:workspace_write"},
			Acceptance: []AcceptanceCriterion{{ID: "changes_made", Description: "必要文件已修改。"}},
		}
		writeNodes = append(writeNodes, node)
		nodes = append(nodes, node)
	}

	// Wire dependencies
	wireDependencies(nodes, analysisNodes, writeNodes, verifyNodes, reviewNodes, strategy)

	// Report node
	reportDeps := []string{"node-002-context"}
	for _, n := range nodes {
		if n.Type == "test" || n.Type == "review" || n.Type == "security" || n.Type == "implementation" || n.Type == "analysis" {
			reportDeps = append(reportDeps, n.ID)
		}
	}
	reportNode := &RunTask{
		ID: fmt.Sprintf("node-%03d-report", len(nodes)+1), Type: "report",
		Title: "整理交付结果", Goal: "汇总完成内容、验证证据、风险和下一步建议。",
		AgentRole: "lead", Dependencies: uniqueStrings(reportDeps),
		Acceptance: []AcceptanceCriterion{{ID: "report_ready", Description: "交付报告已生成。"}},
	}
	nodes = append(nodes, reportNode)

	// Build edges, resources, gates
	var edges []*RunEdge
	for _, n := range nodes {
		for _, dep := range n.Dependencies {
			edges = append(edges, &RunEdge{FromNode: dep, ToNode: n.ID, Type: "depends_on"})
		}
	}
	resources := resourcesFromNodes(nodes)
	var gates []*QualityGate
	for _, n := range nodes {
		if n.Type == "test" || n.Type == "review" || n.Type == "security" {
			gates = append(gates, &QualityGate{ID: "gate-" + n.ID, NodeID: n.ID, Title: n.Title})
		}
	}

	return &RunTaskBoard{
		RunID: runID, ConversationID: conversationID, Strategy: strategy,
		Status: BoardCreated, Nodes: nodes, Edges: edges,
		Resources: resources, Gates: gates, Revision: 1,
		Metadata: map[string]interface{}{"runtime_model": "agent_loop_with_mutable_task_board", "graph_compat": true},
		CreatedAt: now, UpdatedAt: now,
	}, nil
}

func nodeTypeForStage(stageID, role, title string) string {
	text := strings.ToLower(stageID + " " + role + " " + title)
	if strings.Contains(text, "analysis") || strings.Contains(stageID, "分析") {
		return "analysis"
	}
	if strings.Contains(text, "verify") || strings.Contains(text, "test") || strings.Contains(text, "测试") || strings.Contains(text, "验证") {
		return "test"
	}
	if strings.Contains(text, "review") || strings.Contains(text, "reviewer") || strings.Contains(text, "复核") || strings.Contains(text, "审查") {
		return "review"
	}
	if strings.Contains(text, "security") || strings.Contains(text, "安全") {
		return "security"
	}
	if strings.Contains(text, "implement") || strings.Contains(text, "coder") || strings.Contains(text, "代码") || strings.Contains(text, "实现") {
		return "implementation"
	}
	if strings.Contains(text, "plan") || strings.Contains(text, "planner") || strings.Contains(text, "规划") {
		return "plan"
	}
	return "analysis"
}

func wireDependencies(nodes, analysisNodes, writeNodes, verifyNodes, reviewNodes []*RunTask, strategy string) {
	contextNode := "node-002-context"
	for _, n := range analysisNodes {
		if len(n.Dependencies) == 0 {
			n.Dependencies = []string{contextNode}
		}
	}
	analysisIDs := []string{contextNode}
	for _, n := range analysisNodes {
		analysisIDs = append(analysisIDs, n.ID)
	}

	var prevWrite string
	for _, n := range writeNodes {
		if len(n.Dependencies) == 0 {
			if prevWrite != "" {
				n.Dependencies = []string{prevWrite}
			} else {
				n.Dependencies = analysisIDs
			}
		}
		prevWrite = n.ID
	}
	writeTail := prevWrite
	if writeTail == "" && len(analysisIDs) > 0 {
		writeTail = analysisIDs[len(analysisIDs)-1]
	}

	for _, n := range verifyNodes {
		if len(n.Dependencies) == 0 {
			n.Dependencies = []string{writeTail}
		}
	}
	verifyIDs := []string{}
	for _, n := range verifyNodes {
		verifyIDs = append(verifyIDs, n.ID)
	}
	for _, n := range reviewNodes {
		if len(n.Dependencies) == 0 {
			if len(verifyIDs) > 0 {
				n.Dependencies = verifyIDs
			} else {
				n.Dependencies = []string{writeTail}
			}
		}
	}
}

func resourcesFromNodes(nodes []*RunTask) []*ResourceLock {
	lockIDs := make(map[string]bool)
	for _, n := range nodes {
		for _, lock := range n.ResourceLocks {
			if lock != "" {
				lockIDs[lock] = true
			}
		}
	}
	var resources []*ResourceLock
	for id := range lockIDs {
		resources = append(resources, &ResourceLock{ID: id, Status: "free"})
	}
	return resources
}

func safeSlug(value string) string {
	var chars []byte
	lowered := strings.ToLower(strings.ReplaceAll(strings.ReplaceAll(value, "_", "-"), " ", "-"))
	for _, ch := range lowered {
		if (ch >= 'a' && ch <= 'z') || (ch >= '0' && ch <= '9') || ch == '-' {
			chars = append(chars, byte(ch))
		}
	}
	slug := strings.Trim(string(chars), "-")
	if len(slug) > 40 {
		slug = slug[:40]
	}
	if slug == "" {
		return "node"
	}
	return slug
}

func uniqueStrings(items []string) []string {
	seen := make(map[string]bool)
	var result []string
	for _, item := range items {
		if item != "" && !seen[item] {
			seen[item] = true
			result = append(result, item)
		}
	}
	return result
}
