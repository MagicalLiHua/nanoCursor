package server

import (
	"context"
	"encoding/json"

	pb "nanocursor/go-services/mcp/proto"
	"nanocursor/go-services/mcp/internal/mcp"
)

type MCPServiceImpl struct {
	pb.UnimplementedMCPServiceServer
	manager *mcp.Manager
}

func NewMCPServer() *MCPServiceImpl {
	return &MCPServiceImpl{
		manager: mcp.NewManager(),
	}
}

func (s *MCPServiceImpl) Health(ctx context.Context, req *pb.HealthRequest) (*pb.HealthResponse, error) {
	return &pb.HealthResponse{Ok: true, Service: "nanocursor-mcp", Version: "0.1.0"}, nil
}

func (s *MCPServiceImpl) ListPresets(ctx context.Context, req *pb.ListPresetsRequest) (*pb.PresetList, error) {
	presets := s.manager.Presets()
	result := &pb.PresetList{}
	for _, p := range presets {
		result.Presets = append(result.Presets, &pb.Preset{
			Id:          p.ID,
			Name:        p.Name,
			Description: p.Description,
			Command:     p.Command,
			Args:        p.Args,
		})
	}
	return result, nil
}

func (s *MCPServiceImpl) ListServers(ctx context.Context, req *pb.ListServersRequest) (*pb.ServerList, error) {
	servers := s.manager.Servers()
	result := &pb.ServerList{}
	for _, srv := range servers {
		id, _ := srv["server_id"].(string)
		command, _ := srv["command"].(string)
		args, _ := srv["args"].([]string)
		result.Servers = append(result.Servers, &pb.Server{
			Id:      id,
			Command: command,
			Args:    args,
		})
	}
	return result, nil
}

func (s *MCPServiceImpl) ProbeServer(ctx context.Context, req *pb.ProbeRequest) (*pb.ProbeResult, error) {
	env := map[string]string{}
	for k, v := range req.Env {
		env[k] = v
	}
	probeReq := mcp.ProbeRequest{
		ServerID:     req.ServerId,
		WorkspaceDir: req.WorkspaceDir,
		Command:      req.Command,
		Args:         req.Args,
		Env:          env,
		EnvKeys:      req.EnvKeys,
	}
	result := s.manager.Probe(probeReq)
	errMsg := ""
	if !result.Ok {
		for _, c := range result.Checks {
			if c.Status == "failed" {
				errMsg = c.Message
				break
			}
		}
	}
	return &pb.ProbeResult{
		ServerId: result.ServerID,
		Status:   result.Status,
		Ok:       result.Ok,
		Error:    errMsg,
	}, nil
}

func (s *MCPServiceImpl) ListServerTools(ctx context.Context, req *pb.ListToolsRequest) (*pb.ToolList, error) {
	catalog := s.manager.Tools(req.ServerId)
	result := &pb.ToolList{
		Status: catalog.Status,
		Ok:     catalog.Ok,
		Error:  catalog.Error,
	}
	for _, t := range catalog.Tools {
		result.Tools = append(result.Tools, &pb.MCPTool{
			Name:             t.Name,
			Description:      t.Description,
			PermissionLevel:  t.PermissionLevel,
			RequiresApproval: t.RequiresApproval,
		})
	}
	return result, nil
}

func (s *MCPServiceImpl) CallTool(ctx context.Context, req *pb.CallToolRequest) (*pb.CallResult, error) {
	var arguments map[string]any
	if req.Arguments != "" {
		_ = json.Unmarshal([]byte(req.Arguments), &arguments)
	}
	callReq := mcp.CallRequest{
		ServerID:     req.ServerId,
		ToolName:     req.ToolName,
		Arguments:    arguments,
		WorkspaceDir: req.WorkspaceDir,
		RunID:        req.RunId,
	}
	callReq.Policy.PermissionLevel = req.PermissionLevel
	callReq.Policy.RequiresApproval = req.RequiresApproval
	callReq.Policy.ApprovalID = req.ApprovalId
	callReq.Policy.ApprovalToken = req.ApprovalToken

	result := s.manager.Call(callReq)
	ok, _ := result["ok"].(bool)
	serverID, _ := result["server_id"].(string)
	tool, _ := result["tool"].(string)
	resultStr, _ := result["result"].(string)
	errStr, _ := result["error"].(string)
	errCode, _ := result["error_code"].(string)
	level, _ := result["permission_level"].(string)
	reqApproval, _ := result["requires_approval"].(bool)

	return &pb.CallResult{
		ServerId:         serverID,
		Tool:             tool,
		Ok:               ok,
		Result:           resultStr,
		Error:            errStr,
		ErrorCode:        errCode,
		PermissionLevel:  level,
		RequiresApproval: reqApproval,
	}, nil
}
