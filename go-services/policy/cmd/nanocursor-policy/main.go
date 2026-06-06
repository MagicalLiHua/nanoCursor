package main

import (
	"flag"
	"log"
	"net"

	"google.golang.org/grpc"
	"nanocursor/go-services/policy/internal/server"
)

func main() {
	addr := flag.String("addr", ":50052", "gRPC listen address")
	flag.Parse()

	lis, err := net.Listen("tcp", *addr)
	if err != nil {
		log.Fatalf("failed to listen: %v", err)
	}

	s := grpc.NewServer()
	server.RegisterPolicyServer(s, server.NewPolicyServer())

	log.Printf("nanocursor-policy listening on %s", *addr)
	if err := s.Serve(lis); err != nil {
		log.Fatalf("failed to serve: %v", err)
	}
}
