package main

import (
	"log"
	"net"

	"github.com/Jared-Velasquez/tracerca-prod/ingestion/config"
	"github.com/Jared-Velasquez/tracerca-prod/ingestion/handlers"
	"google.golang.org/grpc"
	_ "google.golang.org/grpc/encoding/gzip" // Register gzip compressor since telemetry is compressed by the OTel collector
	otelcolmetrics "go.opentelemetry.io/proto/otlp/collector/metrics/v1"
	otelcoltrace "go.opentelemetry.io/proto/otlp/collector/trace/v1"
)

func main() {
	cfg := config.LoadConfig()

	redisClient, err := handlers.NewRedisClient(cfg.Redis.Addr)
	if err != nil {
		log.Fatalf("failed to connect to Redis: %v", err)
	}

	listener, err := net.Listen("tcp", ":4317")
	if err != nil {
		log.Fatalf("failed to listen: %v", err)
	}

	grpcServer := grpc.NewServer()

	traceServer := handlers.NewTraceServer(cfg, redisClient)
	otelcoltrace.RegisterTraceServiceServer(grpcServer, traceServer)
	log.Println("Traces endpoint enabled")

	metricsServer := handlers.NewMetricsServer(cfg, redisClient)
	otelcolmetrics.RegisterMetricsServiceServer(grpcServer, metricsServer)
	log.Println("Metrics endpoint enabled")

	log.Println("Starting OTLP gRPC server on :4317")

	if err := grpcServer.Serve(listener); err != nil {
		log.Fatalf("failed to serve: %v", err)
	}
}
