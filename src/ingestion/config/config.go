package config

import (
	"os"
)

type Config struct {
	Redis         RedisConfig
	EnableTraces  bool
	EnableMetrics bool
	EnableLogs    bool
}

type RedisConfig struct {
	Addr          string
	TracesStream  string
	MetricsStream string
}

func LoadConfig() *Config {
	return &Config{
		Redis: RedisConfig{
			Addr:          getEnv("REDIS_ADDR", "localhost:6379"),
			TracesStream:  getEnv("REDIS_TRACES_STREAM", "traces"),
			MetricsStream: getEnv("REDIS_METRICS_STREAM", "metrics"),
		},
	}
}

func getEnv(key, defaultValue string) string {
	if val, ok := os.LookupEnv(key); ok && val != "" {
		return val
	}
	return defaultValue
}

func getEnvBool(key string, defaultValue bool) bool {
	if val, ok := os.LookupEnv(key); ok {
		return val == "true" || val == "1"
	}
	return defaultValue
}
