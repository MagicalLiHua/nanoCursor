package taskboard

import "testing"

func TestBuildBoardFeatureDelivery(t *testing.T) {
	plan := `{"strategy":"feature_delivery","stages":[{"id":"intake","title":"接收"},{"id":"analyze","title":"分析","owner_role":"lead"},{"id":"implement","title":"实现","owner_role":"coder"},{"id":"test","title":"测试","owner_role":"tester"}]}`
	board, err := BuildBoard("run-1", plan, "conv-1")
	if err != nil {
		t.Fatal(err)
	}
	if board.Strategy != "feature_delivery" {
		t.Errorf("strategy = %q", board.Strategy)
	}
	if len(board.Nodes) < 4 {
		t.Errorf("expected at least 4 nodes, got %d", len(board.Nodes))
	}
	if board.Task("node-001-intake") == nil {
		t.Error("expected intake node")
	}
	hasReport := false
	for _, n := range board.Nodes {
		if n.Type == "report" {
			hasReport = true
		}
	}
	if !hasReport {
		t.Error("expected report node")
	}
}

func TestBuildBoardDirectReply(t *testing.T) {
	plan := `{"strategy":"lead_direct_reply"}`
	board, err := BuildBoard("run-2", plan, "conv-2")
	if err != nil {
		t.Fatal(err)
	}
	if len(board.Nodes) != 0 {
		t.Errorf("expected 0 nodes for direct reply, got %d", len(board.Nodes))
	}
}

func TestBuildBoardEmptyPlan(t *testing.T) {
	board, err := BuildBoard("run-3", "", "")
	if err != nil {
		t.Fatal(err)
	}
	if board.Strategy != "feature_delivery" {
		t.Errorf("strategy = %q, want feature_delivery", board.Strategy)
	}
}
