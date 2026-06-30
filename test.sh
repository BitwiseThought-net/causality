#!/usr/bin/env bash
set -e

# 1. Use the virtual environment python3 to calculate the signature and construct the curl command
CURL_COMMAND=$(.venv/bin/python3 -c '
import hmac, hashlib, json, os, sys

secret = ""
if os.path.exists(".env"):
    with open(".env") as f:
        for line in f:
            if line.startswith("WEBHOOK_SECRET="):
                secret = line.strip().split("=", 1)[1].encode()

if not secret:
    print("ERROR: WEBHOOK_SECRET not found in .env file.")
    sys.exit(1)

body = b"{\"event\":\"user.created\",\"id\":\"usr_12345\"}"
sig = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()

print(f"curl -s -X POST http://localhost:8042/webhook -H \"Content-Type: application/json\" -H \"X-Hub-Signature-256: {sig}\" -d '\''{{\"event\":\"user.created\",\"id\":\"usr_12345\"}}'\''")
')

if [[ "$CURL_COMMAND" == "ERROR:"* ]]; then
    echo "❌ $CURL_COMMAND Please run ./setup_env.sh first."
    exit 1
fi

echo ""
echo "--------------------------------------------------------"
echo "Generated Test Curl Command:"
echo "--------------------------------------------------------"
echo "$CURL_COMMAND" | sed 's/ -H/ \\\n  -H/g' | sed 's/ -d/ \\\n  -d/g'
echo "--------------------------------------------------------"
echo ""

# 2. Prompt the user for execution choice (Now defaulting to Y)
read -p "Would you like to run this curl command now? [Y/n]: " USER_CHOICE

# Convert to lowercase
USER_CHOICE=$(echo "$USER_CHOICE" | tr '[:upper:]' '[:lower:]')

# Default fallback is now 'y' instead of 'n'
USER_CHOICE="${USER_CHOICE:-y}"

if [[ "$USER_CHOICE" == "y" || "$USER_CHOICE" == "yes" ]]; then
    echo "🚀 Executing request..."
    echo ""
    RESPONSE=$(eval "$CURL_COMMAND")
    echo "📥 Server Response: $RESPONSE"
    echo ""
else
    echo "⏭️ Skipped execution."
    echo ""
fi

