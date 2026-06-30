import os
import sys
import json
import pika
import time

def get_retry_count(properties):
    """Accurately counts how many times this message has cycled through the retry exchange."""
    if properties.headers and 'x-death' in properties.headers:
        death_log = properties.headers['x-death']
        if isinstance(death_log, list) and len(death_log) > 0:
            # Aggregate total count across all routing steps
            return sum(hop.get('count', 0) for hop in death_log)
    return 0

def process_webhook_payload(ch, method, properties, body):
    print("📥 [Worker] Processing an incoming message.")
    retry_count = get_retry_count(properties)
    MAX_RETRIES = 3

    try:
        payload = json.loads(body.decode('utf-8'))

        if payload.get("data", {}).get("trigger_error") is True:
            raise RuntimeError("Simulated internal system dependency crash.")

        print(f"⚙️ [Worker] Task completed successfully: {payload}")
        ch.basic_ack(delivery_tag=method.delivery_tag)

    except Exception as e:
        print(f"⚠️ [Worker] Error processing message: {e}")

        if retry_count >= MAX_RETRIES:
            print(f"🚨 [Worker] Message exceeded {MAX_RETRIES} attempts. Sending to permanent Dead Letter Queue.")

            # Manually publish to the permanent 'dead' path, then clear from the active loop
            ch.basic_publish(
                exchange='dlx_exchange',
                routing_key='dead',
                body=body,
                properties=properties
            )
            ch.basic_ack(delivery_tag=method.delivery_tag)
        else:
            print(f"🔄 [Worker] Attempt {retry_count + 1} failed. Moving to 5s retry backoff stream...")
            # Requeue=False forces RabbitMQ to push the message out to the retry queue configuration
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)

def main():
    RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "localhost")
    RABBITMQ_USER = os.getenv("RABBITMQ_USER")
    RABBITMQ_PASS = os.getenv("RABBITMQ_PASS")

    if not RABBITMQ_USER or not RABBITMQ_PASS:
        print("CRITICAL: RabbitMQ credentials are not set in the worker environment!")
        sys.exit(1)

    credentials = pika.PlainCredentials(username=RABBITMQ_USER, password=RABBITMQ_PASS)
    parameters = pika.ConnectionParameters(host=RABBITMQ_HOST, credentials=credentials)
    connection = pika.BlockingConnection(parameters)
    channel = connection.channel()

    # Mirror identical definitions in worker startup
    channel.exchange_declare(exchange='dlx_exchange', exchange_type='topic')
    channel.queue_declare(
        queue='webhook_queue', 
        durable=True,
        arguments={
            'x-dead-letter-exchange': 'dlx_exchange',
            'x-dead-letter-routing-key': 'retry'
        }
    )
    channel.queue_declare(
        queue='webhook_retry_queue',
        durable=True,
        arguments={
            'x-dead-letter-exchange': '',
            'x-dead-letter-routing-key': 'webhook_queue',
            'x-message-ttl': 5000
        }
    )
    channel.queue_bind(exchange='dlx_exchange', queue='webhook_retry_queue', routing_key='retry')
    channel.queue_declare(queue='webhook_dead_letter_queue', durable=True)
    channel.queue_bind(exchange='dlx_exchange', queue='webhook_dead_letter_queue', routing_key='dead')

    channel.basic_qos(prefetch_count=1)
    channel.basic_consume(queue='webhook_queue', on_message_callback=process_webhook_payload)

    print("🛸 [Worker] Listening for tasks...")
    channel.start_consuming()

if __name__ == '__main__':
    main()

