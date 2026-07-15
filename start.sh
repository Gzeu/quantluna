#!/bin/bash
cd /home/u7/quantluna
rm -f logs/quantluna_2026-07-15.log logs/quantluna_output.log
nohup venv/bin/python main.py --pairs ETHUSDT/SOLUSDT,BTCUSDT/ETHUSDT > logs/quantluna_output.log 2>&1 &
echo "Bot PID: $!"
sleep 12
screen -dmS quantluna-dash venv/bin/python dashboard.py
echo "Dashboard started"
