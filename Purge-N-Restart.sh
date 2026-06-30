#!/bin/bash
# 1. Clear out the bad containers and volumes
docker compose down -v

# 2. Rebuild and launch the aligned code stack
docker compose up --build -d

