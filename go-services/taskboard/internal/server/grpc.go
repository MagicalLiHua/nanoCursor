package server

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"sync"

	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"

	"nanocursor/go-services/taskboard/internal/taskboard"
)

// TaskBoardServiceImpl implements the gRPC TaskBoardServer interface.
type TaskBoardServiceImpl struct {
	UnimplementedTaskBoardServer
	manager *taskboard.BoardManager
	// built holds boards created via BuildBoard RPC, keyed by run_id.
	built map[string]*taskboard.RunTaskBoard
	mu    sync.RWMutex
}

// NewTaskBoardServer returns a new TaskBoardServiceImpl backed by a fresh BoardManager.
func NewTaskBoardServer() *TaskBoardServiceImpl {
	return &TaskBoardServiceImpl{
		manager: taskboard.NewBoardManager(),
		built:   make(map[string]*taskboard.RunTaskBoard),
	}
}

// getBoard returns the board for a run_id, checking the built map first then the manager.
func (s *TaskBoardServiceImpl) getBoard(runID string) *taskboard.RunTaskBoard {
	s.mu.RLock()
	if b, ok := s.built[runID]; ok {
		s.mu.RUnlock()
		return b
	}
	s.mu.RUnlock()
	return s.manager.Get(runID)
}

// getOrCreateBoard returns the board for a run_id or creates a new one via the manager.
// Built boards take priority.
func (s *TaskBoardServiceImpl) getOrCreateBoard(runID string) *taskboard.RunTaskBoard {
	s.mu.RLock()
	if b, ok := s.built[runID]; ok {
		s.mu.RUnlock()
		return b
	}
	s.mu.RUnlock()
	return s.manager.GetOrCreate(runID, "")
}

// --- Conversion helpers ---

func protoToTask(t *TaskMessage) *taskboard.RunTask {
	if t == nil {
		return nil
	}
	var acceptance []taskboard.AcceptanceCriterion
	for _, a := range t.Acceptance {
		acceptance = append(acceptance, taskboard.AcceptanceCriterion{
			ID: a.Id, Description: a.Description, Required: a.Required,
		})
	}
	var retryPolicy taskboard.RetryPolicy
	if t.RetryPolicy != nil {
		retryPolicy = taskboard.RetryPolicy{
			MaxRetries:   int(t.RetryPolicy.MaxRetries),
			RetryCount:   int(t.RetryPolicy.RetryCount),
			FallbackNode: t.RetryPolicy.FallbackNode,
		}
	}

	// Parse JSON-encoded fields.
	var toolPolicy map[string]interface{}
	if t.ToolPolicyJson != "" {
		json.Unmarshal([]byte(t.ToolPolicyJson), &toolPolicy)
	}
	var contextPolicy map[string]interface{}
	if t.ContextPolicyJson != "" {
		json.Unmarshal([]byte(t.ContextPolicyJson), &contextPolicy)
	}
	var outputs []map[string]interface{}
	for _, oj := range t.OutputsJson {
		var m map[string]interface{}
		if json.Unmarshal([]byte(oj), &m) == nil {
			outputs = append(outputs, m)
		}
	}
	var evidence []map[string]interface{}
	for _, ej := range t.EvidenceJson {
		var m map[string]interface{}
		if json.Unmarshal([]byte(ej), &m) == nil {
			evidence = append(evidence, m)
		}
	}

	return &taskboard.RunTask{
		ID: t.Id, Type: t.Type, Title: t.Title, Goal: t.Goal,
		OwnerAgentID: t.OwnerAgentId, AgentRole: t.AgentRole,
		Status: t.Status, Dependencies: t.Dependencies,
		CanParallel: t.CanParallel, WritesFiles: t.WritesFiles,
		ResourceLocks: t.ResourceLocks,
		ToolPolicy:    toolPolicy,
		ContextPolicy: contextPolicy,
		Acceptance:    acceptance,
		Outputs:       outputs,
		Evidence:      evidence,
		RetryPolicy:   retryPolicy,
	}
}

func taskToProto(t *taskboard.RunTask) *TaskMessage {
	if t == nil {
		return nil
	}
	var acceptance []*AcceptanceCriterionMessage
	for _, a := range t.Acceptance {
		acceptance = append(acceptance, &AcceptanceCriterionMessage{
			Id: a.ID, Description: a.Description, Required: a.Required,
		})
	}

	// Marshal JSON-encoded fields.
	var toolPolicyJSON string
	if t.ToolPolicy != nil {
		if data, err := json.Marshal(t.ToolPolicy); err == nil {
			toolPolicyJSON = string(data)
		}
	}
	var contextPolicyJSON string
	if t.ContextPolicy != nil {
		if data, err := json.Marshal(t.ContextPolicy); err == nil {
			contextPolicyJSON = string(data)
		}
	}
	var outputsJSON []string
	for _, o := range t.Outputs {
		if data, err := json.Marshal(o); err == nil {
			outputsJSON = append(outputsJSON, string(data))
		}
	}
	var evidenceJSON []string
	for _, e := range t.Evidence {
		if data, err := json.Marshal(e); err == nil {
			evidenceJSON = append(evidenceJSON, string(data))
		}
	}

	return &TaskMessage{
		Id: t.ID, Type: t.Type, Title: t.Title, Goal: t.Goal,
		OwnerAgentId: t.OwnerAgentID, AgentRole: t.AgentRole,
		Status: t.Status, Dependencies: t.Dependencies,
		CanParallel: t.CanParallel, WritesFiles: t.WritesFiles,
		ResourceLocks:     t.ResourceLocks,
		ToolPolicyJson:    toolPolicyJSON,
		ContextPolicyJson: contextPolicyJSON,
		Acceptance:        acceptance,
		OutputsJson:       outputsJSON,
		EvidenceJson:      evidenceJSON,
		RetryPolicy: &RetryPolicyMessage{
			MaxRetries:   int32(t.RetryPolicy.MaxRetries),
			RetryCount:   int32(t.RetryPolicy.RetryCount),
			FallbackNode: t.RetryPolicy.FallbackNode,
		},
	}
}

func boardToProto(b *taskboard.RunTaskBoard) *BoardStateMessage {
	if b == nil {
		return nil
	}
	var nodes []*TaskMessage
	for _, n := range b.Nodes {
		nodes = append(nodes, taskToProto(n))
	}
	var edges []*EdgeMessage
	for _, e := range b.Edges {
		edges = append(edges, &EdgeMessage{
			FromNode: e.FromNode, ToNode: e.ToNode,
			Type: e.Type, Condition: e.Condition,
		})
	}
	var resources []*ResourceLockMessage
	for _, r := range b.Resources {
		resources = append(resources, &ResourceLockMessage{
			Id: r.ID, OwnerNodeId: r.OwnerNodeID, Status: r.Status,
		})
	}
	var gates []*QualityGateMessage
	for _, g := range b.Gates {
		gates = append(gates, &QualityGateMessage{
			Id: g.ID, NodeId: g.NodeID, Title: g.Title, Status: g.Status,
		})
	}

	// Marshal changelog entries to JSON strings.
	var changeLogJSON []string
	for _, entry := range b.ChangeLog {
		if data, err := json.Marshal(entry); err == nil {
			changeLogJSON = append(changeLogJSON, string(data))
		}
	}

	var metadataJSON string
	if b.Metadata != nil {
		if data, err := json.Marshal(b.Metadata); err == nil {
			metadataJSON = string(data)
		}
	}

	return &BoardStateMessage{
		RunId:          b.RunID,
		ConversationId: b.ConversationID,
		Strategy:       b.Strategy,
		Status:         b.Status,
		Nodes:          nodes,
		Edges:          edges,
		Resources:      resources,
		Gates:          gates,
		Revision:       int32(b.Revision),
		ChangeLogJson:  changeLogJSON,
		MetadataJson:   metadataJSON,
		CreatedAt:      b.CreatedAt,
		UpdatedAt:      b.UpdatedAt,
	}
}

// --- RPC implementations ---

func (s *TaskBoardServiceImpl) AddTask(_ context.Context, req *AddTaskRequest) (*AddTaskResponse, error) {
	if req.RunId == "" {
		return nil, status.Error(codes.InvalidArgument, "run_id is required")
	}
	if req.Task == nil {
		return nil, status.Error(codes.InvalidArgument, "task is required")
	}
	board := s.getOrCreateBoard(req.RunId)
	task := protoToTask(req.Task)
	board.AddTask(task, req.Reason)
	return &AddTaskResponse{}, nil
}

func (s *TaskBoardServiceImpl) UpdateTask(_ context.Context, req *UpdateTaskRequest) (*UpdateTaskResponse, error) {
	if req.RunId == "" {
		return nil, status.Error(codes.InvalidArgument, "run_id is required")
	}
	if req.Task == nil {
		return nil, status.Error(codes.InvalidArgument, "task is required")
	}
	board := s.getOrCreateBoard(req.RunId)
	task := protoToTask(req.Task)
	board.AddTask(task, req.Reason)
	return &UpdateTaskResponse{}, nil
}

func (s *TaskBoardServiceImpl) RemoveTask(_ context.Context, req *RemoveTaskRequest) (*RemoveTaskResponse, error) {
	if req.RunId == "" {
		return nil, status.Error(codes.InvalidArgument, "run_id is required")
	}
	board := s.getBoard(req.RunId)
	if board == nil {
		return nil, status.Errorf(codes.NotFound, "board not found for run_id: %s", req.RunId)
	}
	if err := board.RemoveTask(req.TaskId, req.Reason); err != nil {
		return nil, status.Error(codes.InvalidArgument, err.Error())
	}
	return &RemoveTaskResponse{}, nil
}

func (s *TaskBoardServiceImpl) GetTask(_ context.Context, req *GetTaskRequest) (*GetTaskResponse, error) {
	if req.RunId == "" {
		return nil, status.Error(codes.InvalidArgument, "run_id is required")
	}
	board := s.getBoard(req.RunId)
	if board == nil {
		return &GetTaskResponse{Found: false}, nil
	}
	task := board.Task(req.TaskId)
	if task == nil {
		return &GetTaskResponse{Found: false}, nil
	}
	return &GetTaskResponse{Task: taskToProto(task), Found: true}, nil
}

func (s *TaskBoardServiceImpl) ListTasks(_ context.Context, req *ListTasksRequest) (*ListTasksResponse, error) {
	if req.RunId == "" {
		return nil, status.Error(codes.InvalidArgument, "run_id is required")
	}
	board := s.getBoard(req.RunId)
	if board == nil {
		return &ListTasksResponse{}, nil
	}
	var tasks []*TaskMessage
	for _, n := range board.Nodes {
		tasks = append(tasks, taskToProto(n))
	}
	return &ListTasksResponse{Tasks: tasks}, nil
}

func (s *TaskBoardServiceImpl) ApplyTaskStatus(_ context.Context, req *ApplyTaskStatusRequest) (*ApplyTaskStatusResponse, error) {
	if req.RunId == "" {
		return nil, status.Error(codes.InvalidArgument, "run_id is required")
	}
	board := s.getBoard(req.RunId)
	if board == nil {
		return nil, status.Errorf(codes.NotFound, "board not found for run_id: %s", req.RunId)
	}
	if err := board.ApplyTaskStatus(req.TaskId, req.Status); err != nil {
		return nil, status.Error(codes.InvalidArgument, err.Error())
	}
	return &ApplyTaskStatusResponse{}, nil
}

func (s *TaskBoardServiceImpl) GetReadyNodes(_ context.Context, req *GetReadyNodesRequest) (*GetReadyNodesResponse, error) {
	if req.RunId == "" {
		return nil, status.Error(codes.InvalidArgument, "run_id is required")
	}
	board := s.getBoard(req.RunId)
	if board == nil {
		return &GetReadyNodesResponse{}, nil
	}
	ready := board.ReadyNodes()
	var tasks []*TaskMessage
	for _, n := range ready {
		tasks = append(tasks, taskToProto(n))
	}
	return &GetReadyNodesResponse{Tasks: tasks}, nil
}

func (s *TaskBoardServiceImpl) ConnectTasks(_ context.Context, req *ConnectTasksRequest) (*ConnectTasksResponse, error) {
	if req.RunId == "" {
		return nil, status.Error(codes.InvalidArgument, "run_id is required")
	}
	board := s.getOrCreateBoard(req.RunId)
	if err := board.ConnectTasks(req.UpstreamTask, req.DownstreamTask, req.Reason); err != nil {
		return nil, status.Error(codes.InvalidArgument, err.Error())
	}
	return &ConnectTasksResponse{}, nil
}

func (s *TaskBoardServiceImpl) DisconnectTasks(_ context.Context, req *DisconnectTasksRequest) (*DisconnectTasksResponse, error) {
	if req.RunId == "" {
		return nil, status.Error(codes.InvalidArgument, "run_id is required")
	}
	board := s.getBoard(req.RunId)
	if board == nil {
		return nil, status.Errorf(codes.NotFound, "board not found for run_id: %s", req.RunId)
	}
	if err := board.DisconnectTasks(req.UpstreamTask, req.DownstreamTask, req.Reason); err != nil {
		return nil, status.Error(codes.InvalidArgument, err.Error())
	}
	return &DisconnectTasksResponse{}, nil
}

func (s *TaskBoardServiceImpl) SaveBoard(_ context.Context, req *SaveBoardRequest) (*SaveBoardResponse, error) {
	if req.RunId == "" {
		return nil, status.Error(codes.InvalidArgument, "run_id is required")
	}
	if req.RunDir == "" {
		return nil, status.Error(codes.InvalidArgument, "run_dir is required")
	}
	board := s.getBoard(req.RunId)
	if board == nil {
		return nil, status.Errorf(codes.NotFound, "board not found for run_id: %s", req.RunId)
	}
	path, err := taskboard.SaveBoard(board, req.RunDir)
	if err != nil {
		return nil, status.Error(codes.Internal, err.Error())
	}
	return &SaveBoardResponse{Path: path}, nil
}

func (s *TaskBoardServiceImpl) LoadBoard(_ context.Context, req *LoadBoardRequest) (*LoadBoardResponse, error) {
	if req.RunId == "" {
		return nil, status.Error(codes.InvalidArgument, "run_id is required")
	}
	if req.RunDir == "" {
		return nil, status.Error(codes.InvalidArgument, "run_dir is required")
	}
	board, err := taskboard.LoadBoard(req.RunDir)
	if err != nil {
		return nil, status.Error(codes.Internal, err.Error())
	}
	if board == nil {
		return &LoadBoardResponse{Found: false}, nil
	}
	// Store the loaded board in the built map so subsequent RPCs can access it.
	s.mu.Lock()
	s.built[req.RunId] = board
	s.mu.Unlock()
	return &LoadBoardResponse{Found: true}, nil
}

func (s *TaskBoardServiceImpl) BuildBoard(_ context.Context, req *BuildBoardRequest) (*BuildBoardResponse, error) {
	if req.RunId == "" {
		return nil, status.Error(codes.InvalidArgument, "run_id is required")
	}
	board, err := taskboard.BuildBoard(req.RunId, req.ExecutionPlanJson, req.ConversationId)
	if err != nil {
		return nil, status.Error(codes.Internal, fmt.Sprintf("build board: %v", err))
	}
	// Store the built board.
	s.mu.Lock()
	s.built[req.RunId] = board
	s.mu.Unlock()
	return &BuildBoardResponse{}, nil
}

func (s *TaskBoardServiceImpl) GetBoardState(_ context.Context, req *GetBoardStateRequest) (*GetBoardStateResponse, error) {
	if req.RunId == "" {
		return nil, status.Error(codes.InvalidArgument, "run_id is required")
	}
	board := s.getBoard(req.RunId)
	if board == nil {
		return nil, status.Errorf(codes.NotFound, "board not found for run_id: %s", req.RunId)
	}
	return &GetBoardStateResponse{Board: boardToProto(board)}, nil
}

func (s *TaskBoardServiceImpl) Health(_ context.Context, _ *HealthRequest) (*HealthResponse, error) {
	s.mu.RLock()
	builtCount := len(s.built)
	s.mu.RUnlock()
	total := s.manager.Count() + builtCount
	log.Printf("health check: %d active boards", total)
	return &HealthResponse{
		Ok:           true,
		Service:      "nanocursor-taskboard",
		Version:      "0.1.0",
		ActiveBoards: int32(total),
	}, nil
}
