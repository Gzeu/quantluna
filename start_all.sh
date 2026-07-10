#!/bin/bash
# QuantLuna - Start both main app and dashboard

echo "Starting QuantLuna services..."
echo "==============================="

# Kill any existing instances
pkill -f "python main.py" 2>/dev/null
pkill -f "uvicorn dashboard.server" 2>/dev/null
sleep 1

# Start main application
echo "[1/2] Starting main application (Bybit Live Runner)..."
cd /home/u7/quantluna
.venv/bin/python main.py --skip-health > /tmp/quantluna_app.log 2>&1 &
APP_PID=$!
echo "     Main app started (PID: $APP_PID)"
echo "     Log: /tmp/quantluna_app.log"

# Start dashboard
echo "[2/2] Starting dashboard (FastAPI)..."
.venv/bin/uvicorn dashboard.server:app --host 0.0.0.0 --port 8000 > /tmp/dashboard.log 2>&1 &
DASH_PID=$!
echo "     Dashboard started (PID: $DASH_PID)"
echo "     Log: /tmp/dashboard.log"
echo "     URL: http://localhost:8000"

echo "==============================="
echo "Services running!"
echo "- Main app: dry_run=False, managing open positions"
echo "- Dashboard: http://localhost:8000"
echo ""
echo "To check logs:"
echo "  tail -f /tmp/quantluna_app.log"
echo "  tail -f /tmp/dashboard.log"
echo ""
echo "To stop:"
echo "  pkill -f 'python main.py'"
echo "  pkill -f 'uvicorn dashboard.server'"