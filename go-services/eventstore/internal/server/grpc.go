package server

import (
	"context"

	pb "nanocursor/go-services/eventstore/proto"
	"nanocursor/go-services/eventstore/internal/eventstore"
)

type EventStoreServiceImpl struct {
	pb.UnimplementedEventStoreServiceServer
	store *eventstore.Store
}

func NewEventStoreServer(workspaceDir string) *EventStoreServiceImpl {
	return &EventStoreServiceImpl{
		store: eventstore.NewStore(workspaceDir),
	}
}

func (s *EventStoreServiceImpl) Health(ctx context.Context, req *pb.HealthRequest) (*pb.HealthResponse, error) {
	return &pb.HealthResponse{Ok: true, Service: "nanocursor-eventstore", Version: "0.1.0"}, nil
}

func (s *EventStoreServiceImpl) CreateSession(ctx context.Context, req *pb.CreateSessionRequest) (*pb.Session, error) {
	session := s.store.CreateSession(req.ThreadId, req.Prompt, req.WorkspaceDir, req.Status, req.Mode)
	return sessionToProto(session), nil
}

func (s *EventStoreServiceImpl) GetSession(ctx context.Context, req *pb.GetSessionRequest) (*pb.Session, error) {
	session := s.store.GetSession(req.ThreadId, req.WorkspaceDir)
	if session == nil {
		return &pb.Session{}, nil
	}
	return sessionToProto(session), nil
}

func (s *EventStoreServiceImpl) UpdateSession(ctx context.Context, req *pb.UpdateSessionRequest) (*pb.Session, error) {
	session := s.store.UpdateSession(req.ThreadId, req.WorkspaceDir, req.Changes)
	if session == nil {
		return &pb.Session{}, nil
	}
	return sessionToProto(session), nil
}

func (s *EventStoreServiceImpl) AppendEvent(ctx context.Context, req *pb.AppendEventRequest) (*pb.Event, error) {
	event := s.store.AppendEvent(req.ThreadId, req.EventType, req.Title, req.Content, req.Agent, req.PayloadJson, req.WorkspaceDir)
	return eventToProto(event), nil
}

func (s *EventStoreServiceImpl) ListEvents(ctx context.Context, req *pb.ListEventsRequest) (*pb.EventList, error) {
	events := s.store.ListEvents(req.ThreadId, req.WorkspaceDir, int(req.After))
	result := &pb.EventList{}
	for _, e := range events {
		result.Events = append(result.Events, eventToProto(e))
	}
	return result, nil
}

func (s *EventStoreServiceImpl) CountEvents(ctx context.Context, req *pb.CountEventsRequest) (*pb.CountEventsResponse, error) {
	count := s.store.CountEvents(req.ThreadId, req.WorkspaceDir)
	return &pb.CountEventsResponse{Count: int32(count)}, nil
}

func (s *EventStoreServiceImpl) SubscribeEvents(req *pb.SubscribeEventsRequest, stream pb.EventStoreService_SubscribeEventsServer) error {
	ch := s.store.Subscribe(req.ThreadId)
	defer s.store.Unsubscribe(req.ThreadId, ch)

	for event := range ch {
		if err := stream.Send(eventToProto(&event)); err != nil {
			return err
		}
	}
	return nil
}

func (s *EventStoreServiceImpl) WorkspaceForThread(ctx context.Context, req *pb.WorkspaceForThreadRequest) (*pb.WorkspaceForThreadResponse, error) {
	ws, ok := s.store.WorkspaceForThread(req.ThreadId)
	return &pb.WorkspaceForThreadResponse{WorkspaceDir: ws, Found: ok}, nil
}

func sessionToProto(session *eventstore.Session) *pb.Session {
	return &pb.Session{
		ThreadId:     session.ThreadID,
		WorkspaceDir: session.WorkspaceDir,
		Status:       session.Status,
		Prompt:       session.Prompt,
		Mode:         session.Mode,
		CreatedAt:    session.CreatedAt,
		UpdatedAt:    session.UpdatedAt,
	}
}

func eventToProto(event *eventstore.Event) *pb.Event {
	return &pb.Event{
		Id:          event.ID,
		ThreadId:    event.ThreadID,
		Type:        event.Type,
		Timestamp:   event.Timestamp,
		Agent:       event.Agent,
		Title:       event.Title,
		Content:     event.Content,
		PayloadJson: event.PayloadJSON,
	}
}
