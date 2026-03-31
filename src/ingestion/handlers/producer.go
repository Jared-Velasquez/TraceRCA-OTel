package handlers

import (
	"context"

	"github.com/redis/go-redis/v9"
)

type RedisClient struct {
	client *redis.Client
}

func NewRedisClient(addr string) (*RedisClient, error) {
	client := redis.NewClient(&redis.Options{
		Addr: addr,
	})
	if err := client.Ping(context.Background()).Err(); err != nil {
		return nil, err
	}
	return &RedisClient{client: client}, nil
}

func (r *RedisClient) XAdd(ctx context.Context, stream string, data []byte) error {
	return r.client.XAdd(ctx, &redis.XAddArgs{
		Stream: stream,
		MaxLen: 100000,
		Approx: true,
		Values: map[string]interface{}{
			"data": data,
		},
	}).Err()
}
