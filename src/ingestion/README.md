## Ingestion Server
The Ingestion Server is an OpenTelemetry receiver which implements OTLP over gRPC:

TraceService: `rpc Export(ExportTraceServiceRequest) returns (ExportTraceServiceResponse)`

MetricsService: `rpc Export(ExportMetricsServiceRequest) returns (ExportMetricsServiceResponse)`

LogsService: No implementation for LogsService as this is not necessary for TraceRCA algorithm.

### Ingestion Pipeline
1. Receive Export gRPC request from OpenTelemetry Collector (configured to export to Hermes)
2. Validate request schema
3. Apply message queue backpressure; return gRPC errors if queue is overloaded
4. Enqueue to Kafka for downstream processing (persistent storage)
5. Return ExportResponse

### Miscellaneous
This code has been adapted from the ingest service of the defunct [Jared-Velasquez/hermes-tracing](https://github.com/Jared-Velasquez/hermes-tracing/tree/main/services) project.