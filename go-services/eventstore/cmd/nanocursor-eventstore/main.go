package main

import (
	"flag"
	"log"
	"net"

	"google.golang.org/grpc"
	pb "nanocursor/go-services/eventstore/proto"
	"nanocursor/go-services/eventstore/internal/server"
)

func main() {
	addr := flag.String("addr", ":50058", "gRPC listen address")
	workspace := flag.String("workspace", "", "default workspace directory")
	flag.Parse()

	lis, err := net.Listen("tcp", *addr)
	if err != nil {
		log.Fatalf("failed to listen: %v", err)
	}

	s := grpc.NewServer()
	pb.RegisterEventStoreServiceServer(s, server.NewEventStoreServer(*workspace))

	log.Printf("nanocursor-eventstore listening on %s", *addr)
	if err := s.Serve(lis); err != nil {
		log.Fatalf("failed to serve: %v", err)
	}
}
