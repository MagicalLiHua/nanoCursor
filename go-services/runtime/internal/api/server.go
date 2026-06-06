package api

import (
	"encoding/json"
	"log"
	"net/http"
	"strconv"
	"strings"
	"time"

	"nanocursor/go-runtime/internal/config"
	"nanocursor/go-runtime/internal/mcp"
	"nanocursor/go-runtime/internal/tools"
)

const maxJSONBodyBytes int64 = 1 << 20

type Server struct {
	cfg     config.Config
	tools   *tools.Manager
	mcp     *mcp.Manager
	started time.Time
}

func NewServer(cfg config.Config) *Server {
	return &Server{
		cfg:     cfg,
		tools:   tools.NewManager(),
		mcp:     mcp.NewManager(),
		started: time.Now().UTC(),
	}
}

func (s *Server) Routes() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /health", s.health)
	mux.HandleFunc("POST /v1/tools/preview", s.previewTool)
	mux.HandleFunc("POST /v1/tools/execute", s.executeTool)
	mux.HandleFunc("GET /v1/tools/runs/", s.getToolRun)
	mux.HandleFunc("POST /v1/tools/runs/", s.cancelToolRun)
	mux.HandleFunc("GET /v1/mcp/presets", s.mcpPresets)
	mux.HandleFunc("GET /v1/mcp/servers", s.mcpServers)
	mux.HandleFunc("POST /v1/mcp/servers/probe", s.mcpProbe)
	mux.HandleFunc("GET /v1/mcp/servers/", s.mcpServerTools)
	mux.HandleFunc("POST /v1/mcp/tools/call", s.mcpToolCall)
	return withJSON(mux)
}

func (s *Server) health(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{
		"ok":         true,
		"service":    "nanocursor-runtime",
		"version":    s.cfg.Version,
		"started_at": s.started.Format(time.RFC3339),
		"capabilities": []string{
			"tool.command",
			"tool.tests",
			"mcp.gateway",
			"events.stream",
		},
	})
}

func (s *Server) mcpPresets(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{"presets": s.mcp.Presets()})
}

func (s *Server) mcpServers(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{"servers": s.mcp.Servers()})
}

func (s *Server) mcpProbe(w http.ResponseWriter, r *http.Request) {
	var req mcp.ProbeRequest
	if !decodeJSON(w, r, &req) {
		return
	}
	result := s.mcp.Probe(req)
	writeJSON(w, http.StatusOK, result)
}

func (s *Server) mcpServerTools(w http.ResponseWriter, r *http.Request) {
	rest := strings.TrimPrefix(r.URL.Path, "/v1/mcp/servers/")
	parts := strings.Split(strings.Trim(rest, "/"), "/")
	if len(parts) != 2 || parts[1] != "tools" {
		http.NotFound(w, r)
		return
	}
	writeJSON(w, http.StatusOK, s.mcp.Tools(parts[0]))
}

func (s *Server) mcpToolCall(w http.ResponseWriter, r *http.Request) {
	var req mcp.CallRequest
	if !decodeJSON(w, r, &req) {
		return
	}
	writeJSON(w, http.StatusOK, s.mcp.Call(req))
}

func (s *Server) previewTool(w http.ResponseWriter, r *http.Request) {
	var req tools.PreviewRequest
	if !decodeJSON(w, r, &req) {
		return
	}
	decision := s.tools.Preview(req)
	writeJSON(w, http.StatusOK, decision)
}

func (s *Server) executeTool(w http.ResponseWriter, r *http.Request) {
	var req tools.CommandRequest
	if !decodeJSON(w, r, &req) {
		return
	}
	run, err := s.tools.Execute(req)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": err.Error()})
		return
	}
	status := http.StatusAccepted
	if run.Status == "denied" {
		status = http.StatusForbidden
	}
	writeJSON(w, status, map[string]any{
		"tool_run_id":  run.ToolRunID,
		"status":       run.Status,
		"event_stream": "/v1/tools/runs/" + run.ToolRunID + "/events",
		"error_code":   run.ErrorCode,
		"message":      run.Message,
	})
}

func (s *Server) getToolRun(w http.ResponseWriter, r *http.Request) {
	id, suffix, ok := parseRunPath(r.URL.Path)
	if !ok {
		http.NotFound(w, r)
		return
	}
	if suffix == "events" {
		after := 0
		if rawAfter := r.URL.Query().Get("after"); rawAfter != "" {
			parsed, err := strconv.Atoi(rawAfter)
			if err != nil || parsed < 0 {
				writeJSON(w, http.StatusBadRequest, map[string]any{"error": "invalid after cursor"})
				return
			}
			after = parsed
		}
		events, ok := s.tools.EventsAfter(id, after)
		if !ok {
			http.NotFound(w, r)
			return
		}
		writeJSON(w, http.StatusOK, map[string]any{
			"events": events,
			"cursor": after + len(events),
		})
		return
	}
	run, ok := s.tools.Get(id)
	if !ok {
		http.NotFound(w, r)
		return
	}
	writeJSON(w, http.StatusOK, run)
}

func (s *Server) cancelToolRun(w http.ResponseWriter, r *http.Request) {
	id, suffix, ok := parseRunPath(r.URL.Path)
	if !ok || suffix != "cancel" {
		http.NotFound(w, r)
		return
	}
	if !s.tools.Cancel(id) {
		http.NotFound(w, r)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"tool_run_id": id, "status": "cancelling"})
}

func parseRunPath(path string) (string, string, bool) {
	rest := strings.TrimPrefix(path, "/v1/tools/runs/")
	if rest == path || rest == "" {
		return "", "", false
	}
	parts := strings.Split(strings.Trim(rest, "/"), "/")
	if len(parts) == 1 {
		return parts[0], "", true
	}
	if len(parts) == 2 {
		return parts[0], parts[1], true
	}
	return "", "", false
}

func decodeJSON(w http.ResponseWriter, r *http.Request, out any) bool {
	r.Body = http.MaxBytesReader(w, r.Body, maxJSONBodyBytes)
	defer r.Body.Close()
	if err := json.NewDecoder(r.Body).Decode(out); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "invalid json", "detail": err.Error()})
		return false
	}
	return true
}

func writeJSON(w http.ResponseWriter, status int, body any) {
	w.WriteHeader(status)
	if err := json.NewEncoder(w).Encode(body); err != nil {
		log.Printf("nanocursor-runtime: write json response failed: %v", err)
	}
}

func withJSON(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		next.ServeHTTP(w, r)
	})
}
