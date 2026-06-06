package mcp

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"strconv"
	"strings"
	"sync"

	"nanocursor/go-runtime/internal/supervisor"
)

const protocolVersion = "2024-11-05"

type stdioClient struct {
	process *supervisor.StdioProcess
	stdin   io.WriteCloser
	reader  *bufio.Reader
	mu      sync.Mutex
	nextID  int
}

func newStdioClient(ctx context.Context, cfg ProbeRequest) (*stdioClient, error) {
	process, err := supervisor.StartStdioProcess(ctx, supervisor.StdioProcessSpec{
		Kind:    "mcp_stdio",
		Command: cfg.Command,
		Args:    cfg.Args,
		Cwd:     cfg.WorkspaceDir,
		Env:     cfg.Env,
	}, nil)
	if err != nil {
		return nil, err
	}
	client := &stdioClient{
		process: process,
		stdin:   process.Stdin(),
		reader:  bufio.NewReader(process.Stdout()),
		nextID:  1,
	}
	if _, err := client.request(ctx, "initialize", map[string]any{
		"protocolVersion": protocolVersion,
		"capabilities":    map[string]any{},
		"clientInfo":      map[string]any{"name": "nanocursor-go-runtime", "version": "0.1.0"},
	}); err != nil {
		_ = client.Close()
		if stderr := process.StderrString(); stderr != "" {
			return nil, fmt.Errorf("%w: %s", err, stderr)
		}
		return nil, err
	}
	_ = client.notify(ctx, "notifications/initialized", map[string]any{})
	return client, nil
}

func (c *stdioClient) ListTools(ctx context.Context) ([]ToolInfo, error) {
	result, err := c.request(ctx, "tools/list", map[string]any{})
	if err != nil {
		return nil, err
	}
	rawTools, _ := result["tools"].([]any)
	tools := make([]ToolInfo, 0, len(rawTools))
	for _, item := range rawTools {
		raw, _ := item.(map[string]any)
		name, _ := raw["name"].(string)
		description, _ := raw["description"].(string)
		permission := classifyToolPermission(name)
		tools = append(tools, ToolInfo{
			Name:             name,
			Description:      description,
			PermissionLevel:  permission,
			RequiresApproval: permission != "mcp_read",
		})
	}
	return tools, nil
}

func (c *stdioClient) CallTool(ctx context.Context, toolName string, arguments map[string]any) (map[string]any, error) {
	return c.request(ctx, "tools/call", map[string]any{
		"name":      toolName,
		"arguments": arguments,
	})
}

func (c *stdioClient) Close() error {
	return c.process.Close()
}

func (c *stdioClient) request(ctx context.Context, method string, params map[string]any) (map[string]any, error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	id := c.nextID
	c.nextID++
	message := map[string]any{
		"jsonrpc": "2.0",
		"id":      id,
		"method":  method,
		"params":  params,
	}
	if err := c.writeMessage(message); err != nil {
		return nil, err
	}
	for {
		select {
		case <-ctx.Done():
			return nil, ctx.Err()
		default:
		}
		response, err := c.readMessage()
		if err != nil {
			return nil, err
		}
		responseID, ok := response["id"].(float64)
		if !ok || int(responseID) != id {
			continue
		}
		if rawErr, ok := response["error"].(map[string]any); ok {
			return nil, fmt.Errorf("mcp error: %v", rawErr["message"])
		}
		result, _ := response["result"].(map[string]any)
		if result == nil {
			result = map[string]any{}
		}
		return result, nil
	}
}

func (c *stdioClient) notify(ctx context.Context, method string, params map[string]any) error {
	select {
	case <-ctx.Done():
		return ctx.Err()
	default:
	}
	return c.writeMessage(map[string]any{"jsonrpc": "2.0", "method": method, "params": params})
}

func (c *stdioClient) writeMessage(message map[string]any) error {
	body, err := json.Marshal(message)
	if err != nil {
		return err
	}
	_, err = fmt.Fprintf(c.stdin, "Content-Length: %d\r\n\r\n%s", len(body), body)
	return err
}

func (c *stdioClient) readMessage() (map[string]any, error) {
	headers := map[string]string{}
	for {
		line, err := c.reader.ReadString('\n')
		if err != nil {
			return nil, err
		}
		line = strings.TrimRight(line, "\r\n")
		if line == "" {
			break
		}
		key, value, ok := strings.Cut(line, ":")
		if ok {
			headers[strings.ToLower(strings.TrimSpace(key))] = strings.TrimSpace(value)
		}
	}
	lengthRaw := headers["content-length"]
	if lengthRaw == "" {
		return nil, errors.New("mcp response missing content-length")
	}
	length, err := strconv.Atoi(lengthRaw)
	if err != nil || length <= 0 {
		return nil, errors.New("mcp response has invalid content-length")
	}
	body := make([]byte, length)
	if _, err := io.ReadFull(c.reader, body); err != nil {
		return nil, err
	}
	var payload map[string]any
	if err := json.Unmarshal(body, &payload); err != nil {
		return nil, err
	}
	return payload, nil
}
