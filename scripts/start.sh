#!/bin/bash
# Gas Town — one-command startup after droplet reboot
# Usage: bash ~/gastown/scripts/start.sh
# Then paste the printed tunnel URL into Claude Code to update the dashboard

set -e
cd ~/gastown

echo "=== Gas Town Startup ==="

# Kill old processes
pkill -f "./bridge" 2>/dev/null || true
pkill -f "cloudflared" 2>/dev/null || true
pkill -f "orchestrator.py" 2>/dev/null || true
sleep 1

# Pull latest code
git pull origin main

# Rebuild bridge
cd ~/gastown/bridge && go build -o bridge . && cd ~/gastown
echo "✓ Bridge built"

# Start tmux session
tmux kill-session -t gastown 2>/dev/null || true
tmux new-session -d -s gastown -n bridge
tmux new-window -t gastown -n tunnel
tmux new-window -t gastown -n orch

# Start bridge with auto-restart
tmux send-keys -t gastown:bridge \
  "while true; do cd ~/gastown/bridge && ./bridge; echo 'Bridge crashed — restarting in 2s...'; sleep 2; done" Enter

sleep 2
curl -s localhost:8080/health && echo " ← bridge OK" || echo "BRIDGE FAILED — check tmux bridge window"

# Start cloudflared tunnel
rm -f /tmp/tunnel.log
tmux send-keys -t gastown:tunnel \
  "cloudflared tunnel --url http://localhost:8080 2>&1 | tee /tmp/tunnel.log" Enter

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
  echo "  Paste this URL into Claude Code to update Vercel dashboard"
  echo ""
fi

# Start orchestrator watcher
tmux send-keys -t gastown:orch \
  "cd ~/gastown && python3 orchestrator.py" Enter

echo "✓ Bridge:       running in gastown:bridge (auto-restarts)"
echo "✓ Cloudflared:  running in gastown:tunnel"
echo "✓ Orchestrator: running in gastown:orch"
echo ""
echo "Attach:         tmux attach -t gastown"
echo "Switch windows: Ctrl+B W"
echo "Detach safely:  Ctrl+B D"
