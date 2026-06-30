#!/bin/bash
# Calculate signature for the failing payload and send it automatically
.venv/bin/python3 -c '
import hmac, hashlib, json, os, requests

secret = b""
with open(".env") as f:
    for line in f:
        if line.startswith("WEBHOOK_SECRET="):
            secret = line.strip().split("=", 1)[1].encode()

body = b"{\"event\":\"order.failed_test\",\"data\":{\"id\":\"usr_123\",\"trigger_error\":true}}"
sig = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()

headers = {"Content-Type": "application/json", "X-Hub-Signature-256": sig}
response = requests.post("http://localhost:8042/webhook", data=body, headers=headers)
print(f"📥 Server Response: {response.json()}")
'

