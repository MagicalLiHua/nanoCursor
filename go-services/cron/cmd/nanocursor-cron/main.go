package main

import (
	"flag"
	"log"
	"net"

	"google.golang.org/grpc"
	pb "nanocursor/go-services/cron/proto"
	"nanocursor/go-services/cron/internal/server"
)

func main() {
	addr := flag.String("addr", ":50057", "gRPC listen address")
	workspace := flag.String("workspace", "", "workspace directory for task persistence")
	flag.Parse()

	lis, err := net.Listen("tcp", *addr)
	if err != nil {
		log.Fatalf("failed to listen: %v", err)
	}

	srv := server.NewCronServer()
	if *workspace != "" {
		srv.Init(*workspace)
	}

	s := grpc.NewServer()
	pb.RegisterCronServiceServer(s, srv)

	log.Printf("nanocursor-cron listening on %s", *addr)
	if err := s.Serve(lis); err != nil {
		log.Fatalf("failed to serve: %v", err)
	}
}
