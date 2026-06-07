// Package client provides a public gRPC client wrapper for the policy service.
package client

import (
	"context"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"

	pb "nanocursor/go-services/policy/internal/server"
)

// ActionClassification holds the result of a CheckAction RPC.
type ActionClassification struct {
	Decision    string
	Reason      string
	RiskLevel   string
	CommandType string
}

// CheckAction calls the policy service's CheckAction RPC.
func CheckAction(ctx context.Context, addr, command string) (ActionClassification, error) {
	conn, err := grpc.NewClient(addr, grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		return ActionClassification{}, err
	}
	defer conn.Close()

	client := pb.NewPolicyClient(conn)
	rpcCtx, cancel := context.WithTimeout(ctx, 2*time.Second)
	defer cancel()

	resp, err := client.CheckAction(rpcCtx, &pb.CheckActionRequest{Command: command})
	if err != nil {
		return ActionClassification{}, err
	}

	return ActionClassification{
		Decision:    resp.Decision,
		Reason:      resp.Reason,
		RiskLevel:   resp.RiskLevel,
		CommandType: resp.CommandType,
	}, nil
}
