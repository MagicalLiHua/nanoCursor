package server

import (
	"context"

	pb "nanocursor/go-services/mcp/proto"
)

type MCPServiceImpl struct {
	pb.UnimplementedMCPServiceServer
}

func NewMCPServer() *MCPServiceImpl {
	return &MCPServiceImpl{}
}

func (s *MCPServiceImpl) Health(ctx context.Context, req *pb.HealthRequest) (*pb.HealthResponse, error) {
	return &pb.HealthResponse{Ok: true, Service: "nanocursor-mcp", Version: "0.1.0"}, nil
}
