package handlers

import (
	"context"
	"log"

	"github.com/Jared-Velasquez/tracerca-prod/ingestion/config"
	otelcolmetrics "go.opentelemetry.io/proto/otlp/collector/metrics/v1"
	"google.golang.org/protobuf/proto"
)

type MetricsServer struct {
	otelcolmetrics.UnimplementedMetricsServiceServer
	config *config.Config
	redis  *RedisClient
}

func (s *MetricsServer) Export(ctx context.Context, req *otelcolmetrics.ExportMetricsServiceRequest) (*otelcolmetrics.ExportMetricsServiceResponse, error) {
	data, err := proto.Marshal(req)
	if err != nil {
		log.Printf("Failed to marshal metrics request: %v", err)
		return nil, err
	}

	if err := s.redis.XAdd(ctx, s.config.Redis.MetricsStream, data); err != nil {
		log.Printf("Failed to add metrics to Redis stream: %v", err)
		return nil, err
	}

	return &otelcolmetrics.ExportMetricsServiceResponse{}, nil
}

func NewMetricsServer(config *config.Config, redis *RedisClient) *MetricsServer {
	return &MetricsServer{
		config: config,
		redis:  redis,
	}
}
