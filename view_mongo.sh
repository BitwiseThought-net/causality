#!/usr/bin/env bash
set -e

# Define database parameters matching worker.py
DB_NAME="causality"
COLLECTION_NAME="payloads"
CONTAINER_NAME="causality-mongodb"

echo "📊 Checking MongoDB storage container status..."

# 1. Verify the container is actively running
if ! docker ps --filter "name=$CONTAINER_NAME" --format '{{.Names}}' | grep -q "^$CONTAINER_NAME$"; then
    echo "❌ Error: The Docker container '$CONTAINER_NAME' is not running."
    echo "Please launch your stack first: docker compose up -d"
    exit 1
fi

echo "🔌 Connected to container: $CONTAINER_NAME"
echo "📂 Querying database: $DB_NAME ➔ collection: $COLLECTION_NAME"
echo "----------------------------------------------------------------"

# 2. Execute the query directly inside the container using mongosh
# Uses --quiet to strip out banner metadata text, and .pretty() for scannability
docker exec -i "$CONTAINER_NAME" mongosh "$DB_NAME" --quiet --eval "
    const count = db.$COLLECTION_NAME.countDocuments({});
    print('📦 Total Documents Stored: ' + count + '\n');
    
    if (count > 0) {
        db.$COLLECTION_NAME.find().forEach(doc => {
            print(JSON.stringify(doc, null, 2));
            print('----------------------------------------------------------------');
        });
    } else {
        print('📭 The collection is currently empty. Send a test webhook to populate it.');
    }
"
