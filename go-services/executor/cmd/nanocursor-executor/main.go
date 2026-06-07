package main

import (
	"flag"
	"log"
	"net"

	"google.golang.org/grpc"
	"nanocursor/go-services/executor/internal/server"
	pb "nanocursor/go-services/executor/proto"
)

func main() {
	addr := flag.String("addr", ":50055", "gRPC listen address")
	flag.Parse()

	lis, err := net.Listen("tcp", *addr)
	if err != nil {
		log.Fatalf("failed to listen: %v", err)
	}

	s := grpc.NewServer()
	pb.RegisterExecutorServiceServer(s, server.NewExecutorServer())

	log.Printf("nanocursor-executor listening on %s", *addr)
	if err := s.Serve(lis); err != nil {
		log.Fatalf("failed to serve: %v", err)
	}
}
