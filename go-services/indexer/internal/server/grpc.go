package server

import (
	"context"
	"sync"

	"nanocursor/go-indexer/internal/indexer"
)

// IndexerServiceImpl implements the gRPC Indexer service.
type IndexerServiceImpl struct {
	UnimplementedIndexerServer
	mu      sync.RWMutex
	indexes map[string]*indexer.ProjectIndex
}

// NewIndexerServer creates a new gRPC server instance.
func NewIndexerServer() *IndexerServiceImpl {
	return &IndexerServiceImpl{
		indexes: make(map[string]*indexer.ProjectIndex),
	}
}

// getOrCreateIndex returns an existing index for the workspace or creates a new one.
func (s *IndexerServiceImpl) getOrCreateIndex(workspace string) *indexer.ProjectIndex {
	s.mu.RLock()
	idx, ok := s.indexes[workspace]
	s.mu.RUnlock()
	if ok {
		return idx
	}

	s.mu.Lock()
	defer s.mu.Unlock()
	// Double-check after acquiring write lock.
	if idx, ok = s.indexes[workspace]; ok {
		return idx
	}
	idx = indexer.NewProjectIndex(workspace)
	s.indexes[workspace] = idx
	return idx
}

// BuildIndex handles BuildIndex RPC.
func (s *IndexerServiceImpl) BuildIndex(_ context.Context, req *BuildIndexRequest) (*BuildIndexResponse, error) {
	idx := s.getOrCreateIndex(req.GetWorkspace())
	built, count, err := idx.Build(req.GetForce())
	if err != nil {
		return nil, err
	}
	return &BuildIndexResponse{
		Built:     built,
		FileCount: int32(count),
	}, nil
}

// UpdateIndex handles UpdateIndex RPC.
func (s *IndexerServiceImpl) UpdateIndex(_ context.Context, req *UpdateIndexRequest) (*UpdateIndexResponse, error) {
	idx := s.getOrCreateIndex(req.GetWorkspace())
	updated, removed, err := idx.Update()
	if err != nil {
		return nil, err
	}
	return &UpdateIndexResponse{
		UpdatedCount: int32(updated),
		RemovedCount: int32(removed),
	}, nil
}

// SearchSymbol handles SearchSymbol RPC.
func (s *IndexerServiceImpl) SearchSymbol(_ context.Context, req *SearchSymbolRequest) (*SearchSymbolResponse, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()

	var results []*SymbolResult
	for _, idx := range s.indexes {
		for _, r := range idx.SearchSymbol(req.GetQuery()) {
			results = append(results, &SymbolResult{
				File:       r.File,
				SymbolName: r.SymbolName,
				SymbolType: r.SymbolType,
				Lineno:     int32(r.LineNo),
			})
		}
		if len(results) >= 20 {
			break
		}
	}

	return &SearchSymbolResponse{Results: results}, nil
}

// SearchDependents handles SearchDependents RPC.
func (s *IndexerServiceImpl) SearchDependents(_ context.Context, req *SearchDependentsRequest) (*SearchDependentsResponse, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()

	var files []string
	for _, idx := range s.indexes {
		files = append(files, idx.SearchDependents(req.GetModule())...)
	}
	return &SearchDependentsResponse{Files: files}, nil
}

// GetSummary handles GetSummary RPC.
func (s *IndexerServiceImpl) GetSummary(_ context.Context, req *GetSummaryRequest) (*GetSummaryResponse, error) {
	idx := s.getOrCreateIndex(req.GetWorkspace())
	if _, _, err := idx.Build(false); err != nil {
		return nil, err
	}
	summary := idx.Summary()

	modules := make(map[string]*ModuleInfo)
	for k, v := range summary.Modules {
		symbols := make([]*Symbol, len(v.Symbols))
		for i, sym := range v.Symbols {
			symbols[i] = &Symbol{Name: sym.Name, Type: sym.Type, Lineno: int32(sym.LineNo)}
		}
		modules[k] = &ModuleInfo{Role: v.Role, Symbols: symbols}
	}

	depGraph := make(map[string]*StringList)
	for k, v := range summary.DependencyGraph {
		depGraph[k] = &StringList{Values: v}
	}

	recent := make([]*RecentFile, len(summary.RecentlyModified))
	for i, r := range summary.RecentlyModified {
		recent[i] = &RecentFile{Path: r.Path, Mtime: r.Mtime}
	}

	return &GetSummaryResponse{
		EntryPoints:      summary.EntryPoints,
		SourceCount:      int32(summary.SourceCount),
		TestCount:        int32(summary.TestCount),
		ConfigCount:      int32(summary.ConfigCount),
		TotalFiles:       int32(summary.TotalFiles),
		TotalLoc:         summary.TotalLOC,
		Modules:          modules,
		DependencyGraph:  depGraph,
		RecentlyModified: recent,
		SummaryText:      summary.SummaryText,
	}, nil
}

// GetRouteSummary handles GetRouteSummary RPC.
func (s *IndexerServiceImpl) GetRouteSummary(_ context.Context, req *GetRouteSummaryRequest) (*GetRouteSummaryResponse, error) {
	idx := s.getOrCreateIndex(req.GetWorkspace())
	if _, _, err := idx.Build(false); err != nil {
		return nil, err
	}

	routes := idx.RouteSummary()
	pbRoutes := make([]*RouteEntry, len(routes))
	for i, r := range routes {
		pbRoutes[i] = &RouteEntry{
			Method:  r.Method,
			Path:    r.Path,
			Handler: r.Handler,
			File:    r.File,
			Lineno:  int32(r.LineNo),
		}
	}

	return &GetRouteSummaryResponse{Routes: pbRoutes}, nil
}

// SearchCallers handles SearchCallers RPC.
func (s *IndexerServiceImpl) SearchCallers(_ context.Context, req *SearchCallersRequest) (*SearchCallersResponse, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()

	var callers []string
	for _, idx := range s.indexes {
		callers = append(callers, idx.Callers(req.GetFunctionName())...)
	}
	return &SearchCallersResponse{Callers: callers}, nil
}

// Health handles Health RPC.
func (s *IndexerServiceImpl) Health(_ context.Context, _ *HealthRequest) (*HealthResponse, error) {
	s.mu.RLock()
	totalFiles := int64(0)
	for _, idx := range s.indexes {
		totalFiles += int64(len(idx.Entries()))
	}
	s.mu.RUnlock()

	return &HealthResponse{
		Ok:           true,
		Service:      "nanocursor-indexer",
		Version:      "0.1.0",
		IndexedFiles: totalFiles,
	}, nil
}
