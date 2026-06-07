package main

import (
	"flag"
	"log"
	"net"

	"google.golang.org/grpc"
	pb "nanocursor/go-services/mcp/proto"
	"nanocursor/go-services/mcp/internal/server"
)

func main() {
	addr := flag.String("addr", ":50056", "gRPC listen address")
	flag.Parse()

	lis, err := net.Listen("tcp", *addr)
	if err != nil {
		log.Fatalf("failed to listen: %v", err)
	}

	s := grpc.NewServer()
	pb.RegisterMCPServiceServer(s, server.NewMCPServer())

	log.Printf("nanocursor-mcp listening on %s", *addr)
	if err := s.Serve(lis); err != nil {
		log.Fatalf("failed to serve: %v", err)
	}
}
