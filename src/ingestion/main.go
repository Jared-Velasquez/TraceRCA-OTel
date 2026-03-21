package main

import (
	"log"
	"net"
	"github.com/Jared-Velasquez/tracerca-prod/ingestion/handlers"
	"github.com/Jared-Velasquez/tracerca-prod/ingestion/config"

	"google.golang.org/grpc"
	_ "google.golang.org/grpc/encoding/gzip" // Register gzip compressor since telemetry is compressed by the OTel collector
	// otelcollogs "go.opentelemetry.io/proto/otlp/collector/logs/v1"
	otelcolmetrics "go.opentelemetry.io/proto/otlp/collector/metrics/v1"
	otelcoltrace "go.opentelemetry.io/proto/otlp/collector/trace/v1"
)

func main() {
	cfg := config.LoadConfig()

	listener, err := net.Listen("tcp", ":4317")
	if err != nil {
		log.Fatalf("failed to listen: %v", err)
	}

	// Create a new gRPC server and connect OTLP services
	grpcServer := grpc.NewServer()

	traceServer := handlers.NewTraceServer(cfg)
	otelcoltrace.RegisterTraceServiceServer(grpcServer, traceServer)
	log.Println("Traces endpoint enabled")

	metricsServer := handlers.NewMetricsServer(cfg)
	otelcolmetrics.RegisterMetricsServiceServer(grpcServer, metricsServer)
	log.Println("Metrics endpoint enabled")

	log.Println("Starting OTLP gRPC server on :4317")

	if err := grpcServer.Serve(listener); err != nil {
		log.Fatalf("failed to serve: %v", err)
	}
}