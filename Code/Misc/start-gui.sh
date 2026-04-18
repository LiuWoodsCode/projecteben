#!/usr/bin/env bash

set -e

echo "[1/2] Starting wayvnc..."
wayvnc -g &
WAYVNC_PID=$!

# Give wayvnc a moment to initialize
sleep 2

echo "[2/2] Starting noVNC proxy..."
cd "$HOME/noVNC"
./utils/novnc_proxy --vnc localhost:5900 &

NOVNC_PID=$!

echo
echo "wayvnc PID: $WAYVNC_PID"
echo "noVNC PID:  $NOVNC_PID"
echo "Access noVNC at: http://localhost:6080"

# Optional: wait so script doesn't exit immediately
wait $NOVNC_PID
