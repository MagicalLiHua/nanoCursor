package server

import (
	"context"

	pb "nanocursor/go-services/eventstore/proto"
)

type EventStoreServiceImpl struct {
	pb.UnimplementedEventStoreServiceServer
	workspaceDir string
}

func NewEventStoreServer(workspaceDir string) *EventStoreServiceImpl {
	return &EventStoreServiceImpl{workspaceDir: workspaceDir}
}

func (s *EventStoreServiceImpl) Health(ctx context.Context, req *pb.HealthRequest) (*pb.HealthResponse, error) {
	return &pb.HealthResponse{Ok: true, Service: "nanocursor-eventstore", Version: "0.1.0"}, nil
}
