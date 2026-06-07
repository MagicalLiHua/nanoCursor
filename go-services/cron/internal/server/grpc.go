package server

import (
	"context"

	pb "nanocursor/go-services/cron/proto"
)

type CronServiceImpl struct {
	pb.UnimplementedCronServiceServer
}

func NewCronServer() *CronServiceImpl {
	return &CronServiceImpl{}
}

func (s *CronServiceImpl) Health(ctx context.Context, req *pb.HealthRequest) (*pb.HealthResponse, error) {
	return &pb.HealthResponse{Ok: true, Service: "nanocursor-cron", Version: "0.1.0"}, nil
}
