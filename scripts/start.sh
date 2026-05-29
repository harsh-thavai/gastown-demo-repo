#!/bin/bash
# ============================================================
#  Gas Town — one-command droplet startup
#  Usage:  bash ~/gastown/scripts/start.sh
#  Status: bash ~/gastown/scripts/start.sh --status
#  Stop:   bash ~/gastown/scripts/start.sh --stop
# ============================================================

GASTOWN=~/gastown
VENV="$GASTOWN/.venv"
PYTHON="$VENV/bin/python3"
PIP="$VENV/bin/pip"
UVICORN="$VENV/bin/uvicorn"
TUNNEL_LOG="/tmp/gastown-tunnel.log"
SESSION="gastown"

# ── colours ──────────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✓${NC} $*"; }
warn() { echo -e "${YELLOW}⚠${NC}  $*"; }
err()  { echo -e "${RED}✗${NC} $*"; }
info() { echo -e "${CYAN}→${NC} $*"; }

# ── --status ─────────────────────────────────────────────────
if [ "$1" = "--status" ]; then
  echo -e "\n${BOLD}Gas Town Status${NC}"
  echo "──────────────────────────────"
  pgrep -f "uvicorn main:app" > /dev/null && ok "Bridge   running" || err "Bridge   stopped"
  pgrep -f "orchestrator.py"  > /dev/null && ok "Orch     running" || err "Orch     stopped"
  pgrep -f "cloudflared"      > /dev/null && ok "Tunnel   running" || err "Tunnel   stopped"
  curl -sf localhost:8000/health > /dev/null && ok "Health   OK (port 8000)" || warn "Health   not responding"
  TUNNEL=$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' "$TUNNEL_LOG" 2>/dev/null | tail -1)
  [ -n "$TUNNEL" ] && ok "URL      $TUNNEL" || warn "URL      not found (check $TUNNEL_LOG)"
  echo ""
  exit 0
fi

# ── --stop ───────────────────────────────────────────────────
if [ "$1" = "--stop" ]; then
  info "Stopping all Gas Town processes..."
  tmux kill-session -t "$SESSION" 2>/dev/null && ok "tmux session killed" || true
  pkill -f "uvicorn main:app" 2>/dev/null; pkill -f "cloudflared" 2>/dev/null
  pkill -f "orchestrator.py"  2>/dev/null
  ok "Done"
  exit 0
fi

# ── startup ───────────────────────────────────────────────────
echo -e "\n${BOLD}⛽ Gas Town Startup${NC}"
echo "══════════════════════════════════════"

# 1. Kill any old processes
info "Killing old processes..."
tmux kill-session -t "$SESSION" 2>/dev/null || true
pkill -f "uvicorn main:app" 2>/dev/null || true
pkill -f "cloudflared"      2>/dev/null || true
pkill -f "orchestrator.py"  2>/dev/null || true
sleep 1
ok "Old processes cleared"

# 2. Pull latest code
cd "$GASTOWN"
info "Pulling latest code..."
git fetch origin && git reset --hard origin/main && ok "Code up to date" || warn "git pull failed — using local code"

# 3. Ensure venv + deps
info "Setting up Python environment..."
if [ ! -f "$VENV/bin/activate" ]; then
  python3 -m venv "$VENV" && ok "venv created"
fi
source "$VENV/bin/activate"
pip install -q -r requirements.txt && ok "Python deps ready"

# 4. Check .env
if [ ! -f "$GASTOWN/.env" ]; then
  warn ".env not found — copying from .env.example"
  cp "$GASTOWN/.env.example" "$GASTOWN/.env"
  warn "Edit $GASTOWN/.env and add your API keys, then re-run this script"
fi

# 5. Create tmux session with 3 windows
tmux new-session -d -s "$SESSION" -n bridge
tmux new-window  -t "$SESSION" -n tunnel
tmux new-window  -t "$SESSION" -n orch
ok "tmux session '$SESSION' created"

# 6. Start FastAPI bridge (auto-restarts on crash)
tmux send-keys -t "$SESSION:bridge" \
  "source $VENV/bin/activate && echo 'Starting bridge...' && while true; do cd $GASTOWN/api && $UVICORN main:app --host 0.0.0.0 --port 8000; echo 'Bridge crashed — restarting in 3s...'; sleep 3; done" Enter

# Wait for bridge to be ready (up to 15s)
info "Waiting for bridge to start..."
for i in $(seq 1 15); do
  sleep 1
  if curl -sf localhost:8000/health > /dev/null 2>&1; then
    ok "Bridge ready on port 8000"
    break
  fi
  [ "$i" = "15" ] && err "Bridge not responding — check: tmux attach -t $SESSION:bridge"
done

# 7. Start cloudflared tunnel
rm -f "$TUNNEL_LOG"
tmux send-keys -t "$SESSION:tunnel" \
  "cloudflared tunnel --url http://localhost:8000 2>&1 | tee $TUNNEL_LOG" Enter

# Wait for tunnel URL (up to 30s)
info "Waiting for Cloudflare tunnel URL (up to 30s)..."
TUNNEL=""
for i in $(seq 1 30); do
  sleep 1
  TUNNEL=$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' "$TUNNEL_LOG" 2>/dev/null | head -1)
  [ -n "$TUNNEL" ] && break
done

if [ -n "$TUNNEL" ]; then
  ok "Tunnel ready: $TUNNEL"
  # Auto-write tunnel URL to .env as BRIDGE_URL
  if grep -q "^BRIDGE_URL=" "$GASTOWN/.env" 2>/dev/null; then
    sed -i "s|^BRIDGE_URL=.*|BRIDGE_URL=$TUNNEL/ingest|" "$GASTOWN/.env"
  else
    echo "BRIDGE_URL=$TUNNEL/ingest" >> "$GASTOWN/.env"
  fi
  ok ".env BRIDGE_URL updated automatically"
else
  warn "Tunnel URL not found yet — run: grep trycloudflare $TUNNEL_LOG"
  TUNNEL="(pending)"
fi

# 8. Start orchestrator watcher
tmux send-keys -t "$SESSION:orch" \
  "source $VENV/bin/activate && cd $GASTOWN && python3 orchestrator.py" Enter
sleep 1
ok "Orchestrator started"

# 9. Final summary
echo ""
echo -e "${BOLD}══════════════════════════════════════${NC}"
echo -e "${BOLD}  Gas Town is running!${NC}"
echo "══════════════════════════════════════"
ok "Bridge:       http://localhost:8000"
ok "Orchestrator: watching for tasks"
[ "$TUNNEL" != "(pending)" ] && \
  echo -e "${GREEN}✓${NC} Tunnel:       ${BOLD}$TUNNEL${NC}" || \
  warn "Tunnel:       still connecting..."
echo ""
echo -e "${CYAN}Dashboard:${NC}  https://gastown-demo.vercel.app"
[ "$TUNNEL" != "(pending)" ] && \
  echo -e "${CYAN}Paste in ⚙:${NC} ${BOLD}$TUNNEL${NC}"
echo ""
echo "──────────────────────────────"
echo "  tmux attach -t $SESSION        ← view all windows"
echo "  Ctrl+B W                       ← switch windows"
echo "  Ctrl+B D                       ← detach safely"
echo "  bash start.sh --status         ← check health"
echo "  bash start.sh --stop           ← stop everything"
echo "──────────────────────────────"
echo ""
