#!/usr/bin/env bash
set -e

DB_NAME="causality"
CONTAINER_NAME="causality-mongodb"

echo "🧹 Checking MongoDB storage container status..."

# 1. Verify the container is actively running
if ! docker ps --filter "name=$CONTAINER_NAME" --format '{{.Names}}' | grep -q "^$CONTAINER_NAME$"; then
    echo "❌ Error: The Docker container '$CONTAINER_NAME' is not running."
    echo "Please launch your stack first: docker compose up -d"
    exit 1
fi

echo "⚠️  WARNING: You are about to permanently drop the database: '$DB_NAME'"
read -p "Are you absolutely sure you want to proceed? [Y/n]: " USER_CHOICE

# Convert to lowercase
USER_CHOICE=$(echo "$USER_CHOICE" | tr '[:upper:]' '[:lower:]')

# Default fallback is now 'y' instead of 'n'
USER_CHOICE="${USER_CHOICE:-y}"

if [[ "$USER_CHOICE" == "y" || "$USER_CHOICE" == "yes" ]]; then
    echo "💥 Dropping database..."
    echo ""
    
    # 2. Execute the drop database command inside the container using mongosh
    docker exec -i "$CONTAINER_NAME" mongosh "$DB_NAME" --quiet --eval "
        const result = db.dropDatabase();
        print(JSON.stringify(result, null, 2));
    "
    
    echo ""
    echo "✨ Successfully wiped the '$DB_NAME' database clean!"
    echo "💡 Note: MongoDB will automatically recreate the database the next time your worker inserts a payload."
    echo ""
else
    echo "⏭️  Operation canceled. Database left untouched."
    echo ""
fi
