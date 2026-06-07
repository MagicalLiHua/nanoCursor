package server

import (
	"context"

	"nanocursor/go-services/filetools/internal/filetools"
	pb "nanocursor/go-services/filetools/proto"
)

// FileToolsServiceImpl implements the FileTools gRPC service.
type FileToolsServiceImpl struct {
	pb.UnimplementedFileToolsServer
}

// NewFileToolsServer returns a new FileToolsServiceImpl.
func NewFileToolsServer() *FileToolsServiceImpl {
	return &FileToolsServiceImpl{}
}

func (s *FileToolsServiceImpl) ReadFile(ctx context.Context, req *pb.ReadFileRequest) (*pb.ReadFileResponse, error) {
	content, err := filetools.ReadFile(req.GetWorkspace(), req.GetFilename())
	if err != nil {
		return nil, err
	}
	return &pb.ReadFileResponse{Content: content}, nil
}

func (s *FileToolsServiceImpl) ReadFunction(ctx context.Context, req *pb.ReadFunctionRequest) (*pb.ReadFunctionResponse, error) {
	content, err := filetools.ReadFunction(req.GetWorkspace(), req.GetFilename(), req.GetFunctionName())
	if err != nil {
		return nil, err
	}
	return &pb.ReadFunctionResponse{Content: content}, nil
}

func (s *FileToolsServiceImpl) ReadClass(ctx context.Context, req *pb.ReadClassRequest) (*pb.ReadClassResponse, error) {
	content, err := filetools.ReadClass(req.GetWorkspace(), req.GetFilename(), req.GetClassName())
	if err != nil {
		return nil, err
	}
	return &pb.ReadClassResponse{Content: content}, nil
}

func (s *FileToolsServiceImpl) ReadFileRange(ctx context.Context, req *pb.ReadFileRangeRequest) (*pb.ReadFileRangeResponse, error) {
	content, err := filetools.ReadFileRange(req.GetWorkspace(), req.GetFilename(), int(req.GetStartLine()), int(req.GetEndLine()))
	if err != nil {
		return nil, err
	}
	return &pb.ReadFileRangeResponse{Content: content}, nil
}

func (s *FileToolsServiceImpl) ListDirectory(ctx context.Context, req *pb.ListDirectoryRequest) (*pb.ListDirectoryResponse, error) {
	content, err := filetools.ListDirectory(req.GetWorkspace(), req.GetPath())
	if err != nil {
		return nil, err
	}
	return &pb.ListDirectoryResponse{Content: content}, nil
}

func (s *FileToolsServiceImpl) WriteFile(ctx context.Context, req *pb.WriteFileRequest) (*pb.WriteFileResponse, error) {
	message, err := filetools.WriteFileWithOptions(req.GetWorkspace(), req.GetFilename(), req.GetContent(), filetools.WriteOptions{
		Overwrite:      req.GetOverwrite(),
		BackupExisting: req.GetBackupExisting(),
	})
	if err != nil {
		return nil, err
	}
	return &pb.WriteFileResponse{Message: message}, nil
}

func (s *FileToolsServiceImpl) EditFile(ctx context.Context, req *pb.EditFileRequest) (*pb.EditFileResponse, error) {
	createBackup := req.GetCreateBackup()
	if req.GetMatchMode() == "" && req.GetStartLine() == 0 && req.GetEndLine() == 0 {
		createBackup = true
	}
	result, err := filetools.EditFileWithOptions(req.GetWorkspace(), req.GetFilename(), filetools.EditOptions{
		SearchBlock:  req.GetSearchBlock(),
		ReplaceBlock: req.GetReplaceBlock(),
		StartLine:    int(req.GetStartLine()),
		EndLine:      int(req.GetEndLine()),
		NewText:      req.GetNewText(),
		MatchMode:    req.GetMatchMode(),
		CreateBackup: createBackup,
	})
	if err != nil {
		return nil, err
	}
	return &pb.EditFileResponse{
		Result:           result.Result,
		Diff:             result.Diff,
		Strategy:         result.Strategy,
		MatchedStartLine: int32(result.MatchedStartLine),
		MatchedEndLine:   int32(result.MatchedEndLine),
		ChangedLineCount: int32(result.ChangedLineCount),
		BackupPath:       result.BackupPath,
		Changed:          result.Changed,
	}, nil
}

func (s *FileToolsServiceImpl) BackupFile(ctx context.Context, req *pb.BackupFileRequest) (*pb.BackupFileResponse, error) {
	backupPath := filetools.BackupFile(req.GetWorkspace(), req.GetFilename())
	return &pb.BackupFileResponse{BackupPath: backupPath}, nil
}

func (s *FileToolsServiceImpl) RollbackFile(ctx context.Context, req *pb.RollbackFileRequest) (*pb.RollbackFileResponse, error) {
	message, err := filetools.RollbackFile(req.GetWorkspace(), req.GetFilename(), int(req.GetBackupIndex()))
	if err != nil {
		return nil, err
	}
	return &pb.RollbackFileResponse{Message: message}, nil
}

func (s *FileToolsServiceImpl) ListBackups(ctx context.Context, req *pb.ListBackupsRequest) (*pb.ListBackupsResponse, error) {
	content, err := filetools.ListBackups(req.GetWorkspace(), req.GetFilename())
	if err != nil {
		return nil, err
	}
	return &pb.ListBackupsResponse{Content: content}, nil
}

func (s *FileToolsServiceImpl) Health(ctx context.Context, req *pb.HealthRequest) (*pb.HealthResponse, error) {
	return &pb.HealthResponse{Ok: true, Service: "nanocursor-filetools", Version: "0.1.0"}, nil
}
