package main

import (
	"flag"
	"log"
	"net"

	"google.golang.org/grpc"
	"nanocursor/go-services/filetools/internal/server"
	pb "nanocursor/go-services/filetools/proto"
)

func main() {
	addr := flag.String("addr", ":50054", "gRPC listen address")
	flag.Parse()

	lis, err := net.Listen("tcp", *addr)
	if err != nil {
		log.Fatalf("failed to listen: %v", err)
	}

	s := grpc.NewServer()
	pb.RegisterFileToolsServer(s, server.NewFileToolsServer())

	log.Printf("nanocursor-filetools listening on %s", *addr)
	if err := s.Serve(lis); err != nil {
		log.Fatalf("failed to serve: %v", err)
	}
}
