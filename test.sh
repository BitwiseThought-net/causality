#!/usr/bin/env bash
set -e

SSL=false
USE_TOKEN=false
HOST="localhost"
PORT=":8042"

# 1. Parse the command line arguments
while [[ $# -gt 0 ]]; do
  case $1 in
    -host)
      SSL=true
      HOST="$2"
      PORT=""
      shift 2
      ;;
    -token)
      USE_TOKEN=true
      shift
      ;;
    *)
      echo "❌ Unknown parameter: $1"
      echo "Usage: ./test.sh [-host <hostname>] [-token]"
      exit 1
      ;;
  esac
done

# 2. Inject bash environment parameters cleanly into Python execution
CURL_COMMAND=$(env SSL="$SSL" HOST="$HOST" PORT="$PORT" USE_TOKEN="$USE_TOKEN" .venv/bin/python3 -c '
import hmac, hashlib, json, os, sys

target_ssl = os.getenv("SSL", "false").lower() == "true"
target_host = os.getenv("HOST", "localhost")
target_port = os.getenv("PORT", ":8042")
mock_token_path = os.getenv("USE_TOKEN", "false").lower() == "true"

protocol = "https" if target_ssl else "http"
url = f"{protocol}://{target_host}{target_port}/"

secret = ""
api_key = ""
if os.path.exists(".env"):
    with open(".env") as f:
        for line in f:
            if line.startswith("SECRET="):
                secret = line.strip().split("=", 1)[1].encode()
            if line.startswith("API_KEY="):
                api_key = line.strip().split("=", 1)[1]

if mock_token_path:
    if not api_key:
        print("ERROR: API_KEY not found in .env")
        sys.exit(1)
    body = "{\"event\":\"user.created\",\"id\":\"usr_12345\"}"
    print(f"curl -s -L -X POST {url} -H \"Content-Type: application/json\" -H \"Authorization: Bearer {api_key}\" -d '\''{body}'\''")
else:
    if not secret:
        print("ERROR: SECRET not found in .env")
        sys.exit(1)
    body = b"{\"event\":\"user.created\",\"id\":\"usr_12345\"}"
    sig = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
    print(f"curl -s -L -X POST {url} -H \"Content-Type: application/json\" -H \"X-Hub-Signature-256: {sig}\" -d '\''{body.decode()}'\''")
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

# 3. Prompt the user for execution choice (Defaulting to Y)
read -p "Would you like to run this curl command now? [Y/n]: " USER_CHOICE || true
USER_CHOICE=$(echo "$USER_CHOICE" | tr '[:upper:]' '[:lower:]')
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