package server

import (
	"context"
	"fmt"
	"time"

	"nanocursor/go-services/executor/internal/runner"
	pb "nanocursor/go-services/executor/proto"
)

type ExecutorServiceImpl struct {
	pb.UnimplementedExecutorServiceServer
	manager *runner.RunManager
}

func NewExecutorServer() *ExecutorServiceImpl {
	return &ExecutorServiceImpl{
		manager: runner.NewRunManager(),
	}
}

func (s *ExecutorServiceImpl) Health(ctx context.Context, req *pb.HealthRequest) (*pb.HealthResponse, error) {
	return &pb.HealthResponse{Ok: true, Service: "nanocursor-executor", Version: "0.1.0"}, nil
}

func (s *ExecutorServiceImpl) PreviewTool(ctx context.Context, req *pb.PreviewRequest) (*pb.Decision, error) {
	allowed, level, reqApproval, errorCode, msg, reasons, workspace, resolvedCwd := s.manager.Preview(
		req.Command, req.Cwd, req.WorkspaceDir, req.PermissionLevel,
		req.RequiresApproval, req.ApprovalId, req.ApprovalToken,
	)
	return &pb.Decision{
		Allowed:          allowed,
		PermissionLevel:  level,
		RequiresApproval: reqApproval,
		ErrorCode:        errorCode,
		Message:          msg,
		Reasons:          reasons,
		WorkspaceDir:     workspace,
		Cwd:              resolvedCwd,
	}, nil
}

func (s *ExecutorServiceImpl) ExecuteTool(ctx context.Context, req *pb.ExecuteRequest) (*pb.ToolRun, error) {
	state, err := s.manager.Execute(
		req.Command, req.Cwd, req.WorkspaceDir, int(req.TimeoutMs),
		req.RunId, req.ApprovalToken, req.PermissionLevel,
		req.RequiresApproval, req.ApprovalId,
		int(req.MaxStdoutChars), int(req.MaxStderrChars),
	)
	if err != nil {
		return nil, err
	}
	return stateToProto(state), nil
}

func (s *ExecutorServiceImpl) GetToolRun(ctx context.Context, req *pb.GetToolRunRequest) (*pb.ToolRun, error) {
	state, ok := s.manager.Get(req.Id)
	if !ok {
		return nil, fmt.Errorf("run %s not found", req.Id)
	}
	return stateToProto(state), nil
}

func (s *ExecutorServiceImpl) StreamToolRunEvents(req *pb.StreamEventsRequest, stream pb.ExecutorService_StreamToolRunEventsServer) error {
	cursor := int(req.AfterCursor)
	for {
		events, ok := s.manager.EventsAfter(req.RunId, cursor)
		if !ok {
			return fmt.Errorf("run %s not found", req.RunId)
		}
		for _, ev := range events {
			if err := stream.Send(&pb.ToolEvent{
				Seq:       ev.Seq,
				Type:      ev.Type,
				Timestamp: ev.Timestamp,
				RunId:     ev.RunID,
				Data:      ev.Data,
			}); err != nil {
				return err
			}
			cursor++
		}
		state, ok := s.manager.Get(req.RunId)
		if ok && state.Status != "running" {
			events, _ = s.manager.EventsAfter(req.RunId, cursor)
			for _, ev := range events {
				if err := stream.Send(&pb.ToolEvent{
					Seq:       ev.Seq,
					Type:      ev.Type,
					Timestamp: ev.Timestamp,
					RunId:     ev.RunID,
					Data:      ev.Data,
				}); err != nil {
					return err
				}
			}
			return nil
		}
		time.Sleep(10 * time.Millisecond)
	}
}

func (s *ExecutorServiceImpl) CancelToolRun(ctx context.Context, req *pb.CancelRequest) (*pb.CancelResult, error) {
	ok := s.manager.Cancel(req.RunId)
	if !ok {
		return &pb.CancelResult{Success: false, Message: "run not found or already finished"}, nil
	}
	return &pb.CancelResult{Success: true, Message: "cancelling"}, nil
}

func stateToProto(state *runner.RunState) *pb.ToolRun {
	return &pb.ToolRun{
		Id:              state.ID,
		Status:          state.Status,
		Command:         state.Command,
		Cwd:             state.Cwd,
		ExitCode:        int32(state.ExitCode),
		Stdout:          state.Stdout,
		Stderr:          state.Stderr,
		DurationMs:      state.DurationMS,
		TimedOut:        state.TimedOut,
		ErrorCode:       state.ErrorCode,
		Message:         state.Message,
		StdoutTruncated: state.StdoutTruncated,
		StderrTruncated: state.StderrTruncated,
	}
}
