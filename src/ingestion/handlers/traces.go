package handlers

import (
	"log"
	"context"
	"github.com/Jared-Velasquez/tracerca-prod/ingestion/config"

	otelcoltrace "go.opentelemetry.io/proto/otlp/collector/trace/v1"
	"google.golang.org/protobuf/proto"
)

type TraceServer struct {
	otelcoltrace.UnimplementedTraceServiceServer
	config *config.Config
}

func (s *TraceServer) Export(ctx context.Context, req *otelcoltrace.ExportTraceServiceRequest) (*otelcoltrace.ExportTraceServiceResponse, error) {

	// Use protobuf serialization to marshal the request
	data, err := proto.Marshal(req)
	if err != nil {
		log.Printf("Failed to marshal trace request: %v", err)
		return nil, err
	}

	// TODO: Should I use dependency injection for producer?
	producer := NewProducer(s.config.Kafka.Broker, s.config.Kafka.TracesTopic)
	if err := producer.Send(data); err != nil {
		log.Printf("Failed to send trace data to Kafka: %v", err)
		return nil, err
	}


	// 4. Return successful response to client

	return &otelcoltrace.ExportTraceServiceResponse{}, nil
}

func NewTraceServer(config *config.Config) *TraceServer {
	return &TraceServer{
		config: config,
	}
}