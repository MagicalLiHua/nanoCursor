package server

import (
	"context"

	pb "nanocursor/go-services/executor/proto"
)

// ExecutorServiceImpl implements the ExecutorService gRPC service.
type ExecutorServiceImpl struct {
	pb.UnimplementedExecutorServiceServer
}

// NewExecutorServer returns a new ExecutorServiceImpl.
func NewExecutorServer() *ExecutorServiceImpl {
	return &ExecutorServiceImpl{}
}

func (s *ExecutorServiceImpl) Health(ctx context.Context, req *pb.HealthRequest) (*pb.HealthResponse, error) {
	return &pb.HealthResponse{Ok: true, Service: "nanocursor-executor", Version: "0.1.0"}, nil
}
