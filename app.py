import os
import hmac
import hashlib
import json
import pika
from fastapi import FastAPI, Request, HTTPException, status
import uvicorn

app = FastAPI(title="Secure Webhook Queue Ingester")

WEBHOOK_SECRET_STR = os.getenv("WEBHOOK_SECRET", "")
WEBHOOK_SECRET = WEBHOOK_SECRET_STR.encode("utf-8")
RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "localhost")
RABBITMQ_USER = os.getenv("RABBITMQ_USER")
RABBITMQ_PASS = os.getenv("RABBITMQ_PASS")

if not WEBHOOK_SECRET_STR:
    raise RuntimeError("CRITICAL STARTUP ERROR: The 'WEBHOOK_SECRET' environment variable is missing or empty!")
if not RABBITMQ_USER:
    raise RuntimeError("CRITICAL STARTUP ERROR: The 'RABBITMQ_USER' environment variable is missing or empty!")
if not RABBITMQ_PASS:
    raise RuntimeError("CRITICAL STARTUP ERROR: The 'RABBITMQ_PASS' environment variable is missing or empty!")

@app.post("/webhook")
async def receive_webhook(request: Request):
    signature = request.headers.get("X-Hub-Signature-256")
    if not signature:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing signature")
        
    body = await request.body()
    expected_signature = "sha256=" + hmac.new(WEBHOOK_SECRET, body, hashlib.sha256).hexdigest()
    
    if not hmac.compare_digest(signature, expected_signature):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid signature")

    try:
        credentials = pika.PlainCredentials(username=RABBITMQ_USER, password=RABBITMQ_PASS)
        parameters = pika.ConnectionParameters(host=RABBITMQ_HOST, credentials=credentials)
        connection = pika.BlockingConnection(parameters)
        channel = connection.channel()
        
        # 1. Setup Dead Letter Exchange
        channel.exchange_declare(exchange='dlx_exchange', exchange_type='topic')
        
        # 2. Main Processing Queue (routes failures to dlx_exchange with 'retry' routing key)
        channel.queue_declare(
            queue='webhook_queue', 
            durable=True,
            arguments={
                'x-dead-letter-exchange': 'dlx_exchange',
                'x-dead-letter-routing-key': 'retry'
            }
        )
        
        # 3. Retry Queue (Holds messages for 5000ms, then drops them back into the main queue)
        channel.queue_declare(
            queue='webhook_retry_queue',
            durable=True,
            arguments={
                'x-dead-letter-exchange': '', # Default exchange
                'x-dead-letter-routing-key': 'webhook_queue', # Routes straight back home
                'x-message-ttl': 5000 # 5-second backoff delay
            }
        )
        channel.queue_bind(exchange='dlx_exchange', queue='webhook_retry_queue', routing_key='retry')
        
        # 4. Permanent Error Queue (Holds messages after they exceed maximum attempts)
        channel.queue_declare(queue='webhook_dead_letter_queue', durable=True)
        channel.queue_bind(exchange='dlx_exchange', queue='webhook_dead_letter_queue', routing_key='dead')
        
        # Publish payload initially to primary queue
        channel.basic_publish(
            exchange='',
            routing_key='webhook_queue',
            body=body,
            properties=pika.BasicProperties(delivery_mode=pika.DeliveryMode.Persistent)
        )
        connection.close()
        
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
    
    return {"status": "queued", "message": "Webhook received and safely buffered."}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=80)

