#!/bin/bash
echo "Building Docker Container..."
docker-compose build

echo "Starting Bot..."
docker-compose up -d

echo "Logs available at ./logs/app.log"
docker-compose logs -f