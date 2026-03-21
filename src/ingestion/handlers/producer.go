package handlers

import (
	"log"
	"context"
	"time"

	kafka "github.com/segmentio/kafka-go"
)

type Producer struct {
	Broker string
	Topic  string
}

func NewProducer(broker, topic string) *Producer {
	return &Producer{
		Broker: broker,
		Topic:  topic,
	}
}

func (p *Producer) Send(data []byte) error {
	conn, err := kafka.DialLeader(context.Background(), "tcp", p.Broker, p.Topic, 0)
	if err != nil {
		log.Printf("Failed to dial Kafka leader: %v", err)
		return err
	}

	conn.SetWriteDeadline(time.Now().Add(10 * time.Second))

	_, err = conn.WriteMessages(kafka.Message{Value: data})
	if err != nil {
		log.Printf("Failed to write messages to Kafka: %v", err)
		return err
	}

	if err := conn.Close(); err != nil {
		log.Printf("Failed to close Kafka connection: %v", err)
		return err
	}
	return nil
}