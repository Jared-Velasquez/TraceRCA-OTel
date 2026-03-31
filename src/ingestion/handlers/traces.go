package handlers

import (
	"context"
	"log"

	"github.com/Jared-Velasquez/tracerca-prod/ingestion/config"
	otelcoltrace "go.opentelemetry.io/proto/otlp/collector/trace/v1"
	"google.golang.org/protobuf/proto"
)

type TraceServer struct {
	otelcoltrace.UnimplementedTraceServiceServer
	config *config.Config
	redis  *RedisClient
}

func (s *TraceServer) Export(ctx context.Context, req *otelcoltrace.ExportTraceServiceRequest) (*otelcoltrace.ExportTraceServiceResponse, error) {
	data, err := proto.Marshal(req)
	if err != nil {
		log.Printf("Failed to marshal trace request: %v", err)
		return nil, err
	}

	if err := s.redis.XAdd(ctx, s.config.Redis.TracesStream, data); err != nil {
		log.Printf("Failed to add trace to Redis stream: %v", err)
		return nil, err
	}

	return &otelcoltrace.ExportTraceServiceResponse{}, nil
}

func NewTraceServer(config *config.Config, redis *RedisClient) *TraceServer {
	return &TraceServer{
		config: config,
		redis:  redis,
	}
}
