#!/bin/bash
tmux kill-session -t gastown 2>/dev/null || true

tmux new-session  -d -s gastown -n bridge
tmux send-keys    -t gastown:bridge \
  "cd ~/gastown/bridge && ./bridge 2>&1 | tee /tmp/bridge.log" Enter

tmux new-window   -t gastown -n mayor
tmux send-keys    -t gastown:mayor \
  "cd ~/gastown && python3 orchestrator.py" Enter

tmux new-window   -t gastown -n dashboard
tmux send-keys    -t gastown:dashboard \
  "cd ~/gastown/dashboard && python3 -m http.server 3000" Enter

tmux new-window   -t gastown -n logs
tmux send-keys    -t gastown:logs \
  "tail -f /tmp/bridge.log" Enter

tmux attach -t gastown

echo ""
echo "⛽  Gas Town running"
echo "    Bridge:    http://localhost:8080/health"
echo "    Dashboard: http://localhost:3000"
echo "    Trigger:   curl -X POST localhost:8080/demo/start"
