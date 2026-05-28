#!/bin/bash
# Gas Town — one-command startup after droplet reboot
# Usage: bash ~/gastown/scripts/start.sh
# Then paste the printed tunnel URL into the dashboard settings

set -e
GASTOWN=~/gastown
cd "$GASTOWN"

echo "=== Gas Town Startup ==="

# Kill old processes
pkill -f "uvicorn" 2>/dev/null || true
pkill -f "cloudflared" 2>/dev/null || true
pkill -f "orchestrator.py" 2>/dev/null || true
sleep 1

# Pull latest code
git pull origin main

# Ensure venv exists and deps are installed
if [ ! -f "$GASTOWN/.venv/bin/activate" ]; then
  python3 -m venv "$GASTOWN/.venv"
fi
source "$GASTOWN/.venv/bin/activate"
pip install -q -r requirements.txt
echo "✓ Python deps ready"

# Start tmux session
tmux kill-session -t gastown 2>/dev/null || true
tmux new-session -d -s gastown -n bridge
tmux new-window -t gastown -n tunnel
tmux new-window -t gastown -n orch

# Start FastAPI bridge — run from api/ directory to avoid module path issues
tmux send-keys -t gastown:bridge \
  "source $GASTOWN/.venv/bin/activate && while true; do cd $GASTOWN/api && uvicorn main:app --host 0.0.0.0 --port 8000; echo 'Bridge crashed — restarting in 2s...'; sleep 2; done" Enter

sleep 2
curl -s localhost:8000/health && echo " ← bridge OK" || echo "BRIDGE FAILED — check tmux bridge window"

# Start cloudflared tunnel
rm -f /tmp/tunnel.log
tmux send-keys -t gastown:tunnel \
  "cloudflared tunnel --url http://localhost:8000 2>&1 | tee /tmp/tunnel.log" Enter

echo "Waiting for tunnel URL (10s)..."
sleep 10
TUNNEL=$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' /tmp/tunnel.log | head -1)

if [ -z "$TUNNEL" ]; then
  echo "Tunnel not ready yet. Run: grep trycloudflare /tmp/tunnel.log"
else
  echo ""
  echo "=========================================="
  echo "  TUNNEL: $TUNNEL"
  echo "=========================================="
  echo "  Enter this URL in the dashboard Settings (gear icon)"
  echo ""
fi

# Start orchestrator watcher
tmux send-keys -t gastown:orch \
  "source $GASTOWN/.venv/bin/activate && cd $GASTOWN && python3 orchestrator.py" Enter

echo "✓ FastAPI Bridge: running in gastown:bridge (port 8000, auto-restarts)"
echo "✓ Cloudflared:    running in gastown:tunnel"
echo "✓ Orchestrator:   running in gastown:orch"
echo ""
echo "Attach:         tmux attach -t gastown"
echo "Switch windows: Ctrl+B W"
echo "Detach safely:  Ctrl+B D"
