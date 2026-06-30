#!/bin/bash
set -e

# Default number of iterations if parameter is omitted
NUM_ITEMS=10

# 1. Parse the command line argument for -num
while [[ $# -gt 0 ]]; do
  case $1 in
    -num)
      NUM_ITEMS="$2"
      shift 2
      ;;
    *)
      echo "❌ Unknown parameter: $1"
      echo "Usage: ./bulk_load.sh -num <number_of_items>"
      exit 1
      ;;
  esac
done

# Validate that the argument is a positive integer
if ! [[ "$NUM_ITEMS" =~ ^[0-9]+$ ]] || [ "$NUM_ITEMS" -le 0 ]; then
  echo "❌ Error: -num must be a positive integer."
  exit 1
fi

# 2. Use the virtual environment Python to generate the single curl payload template and signature
# Since all bodies are identical for this test, generating the signature once keeps the loop blindingly fast.
SETUP_DATA=$(.venv/bin/python3 -c '
import hmac, hashlib, json, os, sys

secret = ""
if os.path.exists(".env"):
    with open(".env") as f:
        for line in f:
            if line.startswith("WEBHOOK_SECRET="):
                secret = line.strip().split("=", 1)[1].encode()

if not secret:
    print("ERROR")
    sys.exit(1)

body = b"{\"event\":\"bulk.load_test\",\"data\":{\"id\":\"bulk_item\",\"type\":\"benchmark\"}}"
sig = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()

# Print values separated by a custom delimiter for bash reading
print(f"{sig}|||{body.decode()}")
')

if [ "$SETUP_DATA" = "ERROR" ]; then
    echo "❌ Error: WEBHOOK_SECRET not found in .env file. Run ./setup_env.sh first."
    exit 1
fi

# Split out signature and payload body
SIGNATURE=$(echo "$SETUP_DATA" | awk -F '|||' '{print $1}')
PAYLOAD_BODY=$(echo "$SETUP_DATA" | awk -F '|||' '{print $2}')

echo "🚀 Bombarding webhook endpoint with $NUM_ITEMS items..."
echo "📊 Signature used: $SIGNATURE"
echo "--------------------------------------------------------"

# 3. Fire the rapid loop using curl
START_TIME=$(date +%s)

for ((i=1; i<=NUM_ITEMS; i++)); do
  # Sends request silently to maximize terminal performance
  curl -s -o /dev/null -w "" \
    -X POST http://localhost:8042/webhook \
    -H "Content-Type: application/json" \
    -H "X-Hub-Signature-256: $SIGNATURE" \
    -d "$PAYLOAD_BODY"
    
  # Visual simple counter progress updates every 50 entries
  if (( i % 50 == 0 )) || (( i == NUM_ITEMS )); then
     echo "✅ Sent $i / $NUM_ITEMS items..."
  fi
done

END_TIME=$(date +%s)
DURATION=$((END_TIME - START_TIME))

echo "--------------------------------------------------------"
echo "🏁 Done! Successfully injected $NUM_ITEMS items into the pipeline in ${DURATION}s."
