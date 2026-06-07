package server

import (
	"context"
	"path/filepath"

	pb "nanocursor/go-services/cron/proto"
	"nanocursor/go-services/cron/internal/cron"
)

type CronServiceImpl struct {
	pb.UnimplementedCronServiceServer
	scheduler *cron.Scheduler
}

func NewCronServer() *CronServiceImpl {
	return &CronServiceImpl{}
}

// Init initializes the scheduler with a workspace path. Call before Serve.
func (s *CronServiceImpl) Init(workspaceDir string) {
	path := filepath.Join(workspaceDir, ".claude", "scheduled_tasks.json")
	s.scheduler = cron.NewScheduler(path)
	s.scheduler.LoadFromFile()
	s.scheduler.Start()
}

// Stop shuts down the scheduler tick loop.
func (s *CronServiceImpl) Stop() {
	if s.scheduler != nil {
		s.scheduler.Stop()
	}
}

func (s *CronServiceImpl) Health(ctx context.Context, req *pb.HealthRequest) (*pb.HealthResponse, error) {
	return &pb.HealthResponse{Ok: true, Service: "nanocursor-cron", Version: "0.1.0"}, nil
}

func (s *CronServiceImpl) CreateTask(ctx context.Context, req *pb.CreateTaskRequest) (*pb.Task, error) {
	id := s.scheduler.Create(req.CronExpr, req.Prompt, req.Recurring, req.Durable)
	for _, t := range s.scheduler.ListAll() {
		if t.ID == id {
			return taskToProto(t), nil
		}
	}
	return &pb.Task{Id: id}, nil
}

func (s *CronServiceImpl) DeleteTask(ctx context.Context, req *pb.DeleteTaskRequest) (*pb.DeleteTaskResponse, error) {
	ok := s.scheduler.Delete(req.TaskId)
	if !ok {
		return &pb.DeleteTaskResponse{Success: false, Message: "task not found"}, nil
	}
	return &pb.DeleteTaskResponse{Success: true, Message: "deleted"}, nil
}

func (s *CronServiceImpl) ListTasks(ctx context.Context, req *pb.ListTasksRequest) (*pb.TaskList, error) {
	tasks := s.scheduler.ListAll()
	result := &pb.TaskList{}
	for _, t := range tasks {
		result.Tasks = append(result.Tasks, taskToProto(t))
	}
	return result, nil
}

func (s *CronServiceImpl) DrainEvents(req *pb.DrainEventsRequest, stream pb.CronService_DrainEventsServer) error {
	for {
		events := s.scheduler.DrainEvents()
		for _, ev := range events {
			if err := stream.Send(&pb.CronEvent{
				Type:      ev.Type,
				TaskId:    ev.TaskID,
				Prompt:    ev.Prompt,
				Recurring: ev.Recurring,
				FiredAt:   ev.FiredAt,
			}); err != nil {
				return err
			}
		}
	}
}

func taskToProto(t *cron.CronTask) *pb.Task {
	var lastFired int64
	if !t.LastFiredAt.IsZero() {
		lastFired = t.LastFiredAt.Unix()
	}
	return &pb.Task{
		Id:          t.ID,
		CronExpr:    t.CronExpr,
		Prompt:      t.Prompt,
		Recurring:   t.Recurring,
		Durable:     t.Durable,
		CreatedAt:   t.CreatedAt.Unix(),
		LastFiredAt: lastFired,
		Status:      "active",
	}
}
