# Gas Town — Full Build Prompt for Claude Code
# Paste this entire file as your first message. Build in order: Step 1 → 2 → 3 → 4 → 5.

---

## WHAT YOU ARE BUILDING

Real multi-agent orchestration. Not simulated. Every claim verifiable.

```
Standup notes (browser input)
    → POST /task → Go SSE bridge
    → orchestrator.py (The Mayor)
        → calls DO Inference to parse notes into tasks
        → spawns 5 Claude Code sessions in tmux (one per worktree)
              polecat-auth    writes JWT middleware
              polecat-tests   writes tests
              polecat-debug   runs gstack /cso (OWASP+STRIDE audit)
              polecat-docs    updates README
              polecat-review  runs gstack /review on all diffs
        → each agent: git commit → git push → real GitHub PR
        → Mayor fires events throughout → SSE bridge → dashboard updates live
    → Vercel REST API triggers deployment after PR merges
    → live URL appears in dashboard topbar
```

Stack:
- DigitalOcean Droplet — compute
- DO Serverless Inference `https://inference.do-ai.run/v1` — model backend (model: `meta-llama-3.3-70b-instruct`)
- Claude Code CLI — each polecat is a real `claude` session in a tmux pane
- gstack skills (`~/.claude/skills/gstack`) — methodology for polecat-debug and polecat-review
- Go SSE bridge — real-time event stream to browser
- Single HTML dashboard (`gastown-dashboard.html`) — already designed, wire to real SSE
- `gh` CLI — opens real GitHub PRs
- Vercel REST API — deploys dashboard after every convoy

---

## DIRECTORY STRUCTURE

```
~/gastown/
├── orchestrator.py
├── bridge/
│   ├── main.go
│   └── go.mod
├── dashboard/
│   └── index.html
├── scripts/
│   ├── setup-worktrees.sh
│   └── start.sh
└── demo-repo/
    ├── src/api/server.go
    ├── src/auth/
    ├── tests/
    └── README.md
```

Worktrees (hardcoded paths):
```
~/gastown/wt-auth    → branch polecat/auth
~/gastown/wt-tests   → branch polecat/tests
~/gastown/wt-debug   → branch polecat/debug
~/gastown/wt-docs    → branch polecat/docs
~/gastown/wt-review  → branch polecat/review
```

---

## STEP 1: orchestrator.py

Build this file completely. It is the Mayor — the heart of the demo.

### Config block at top:
```python
import os, json, subprocess, time, threading, requests
from datetime import datetime

DO_INFERENCE_URL = os.environ["DO_INFERENCE_URL"]   # https://inference.do-ai.run/v1
MODEL_ACCESS_KEY = os.environ["MODEL_ACCESS_KEY"]   # sk-do-...
BRIDGE_URL       = "http://localhost:8080/ingest"
BRIDGE_SECRET    = os.environ.get("BRIDGE_SECRET", "gastown-demo-2026")
VERCEL_TOKEN     = os.environ.get("VERCEL_TOKEN", "")
VERCEL_PROJECT   = os.environ.get("VERCEL_PROJECT", "gastown-demo")
MODEL            = "meta-llama-3.3-70b-instruct"

WORKTREES = {
    "polecat-auth":   os.path.expanduser("~/gastown/wt-auth"),
    "polecat-tests":  os.path.expanduser("~/gastown/wt-tests"),
    "polecat-debug":  os.path.expanduser("~/gastown/wt-debug"),
    "polecat-docs":   os.path.expanduser("~/gastown/wt-docs"),
    "polecat-review": os.path.expanduser("~/gastown/wt-review"),
}
```

### `emit(agent, event_type, text, diff=None)`
```python
def emit(agent, event_type, text, diff=None):
    payload = {
        "agent": agent,
        "agent_role": agent.replace("polecat-", ""),
        "type": event_type,
        "text": text,
        "time": datetime.now().strftime("%H:%M:%S"),
    }
    if diff:
        payload["diff"] = diff
    try:
        requests.post(BRIDGE_URL, json=payload,
                      headers={"X-Bridge-Secret": BRIDGE_SECRET}, timeout=2)
    except:
        pass
    print(f"[{payload['time']}] [{agent}] {event_type}: {text}")
```

### `call_do_inference(system, user) -> str`
```python
def call_do_inference(system, user):
    resp = requests.post(
        f"{DO_INFERENCE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {MODEL_ACCESS_KEY}",
                 "Content-Type": "application/json"},
        json={"model": MODEL,
              "messages": [{"role": "system", "content": system},
                           {"role": "user",   "content": user}],
              "max_tokens": 2000, "temperature": 0.3}
    )
    return resp.json()["choices"][0]["message"]["content"]
```

### `parse_meeting_notes(notes) -> dict`
```python
def parse_meeting_notes(notes):
    emit("mayor", "TASK_STARTED", "Parsing meeting notes...")
    system = """You are The Mayor — orchestration coordinator for Gas Town.
Parse meeting notes into a convoy plan.
Respond ONLY with valid JSON, no markdown fences:
{
  "convoy": "sprint name 2-4 words",
  "tasks": [
    {
      "agent": "polecat-auth|polecat-tests|polecat-debug|polecat-docs|polecat-review",
      "task": "specific single coding task, one sentence",
      "priority": "HIGH|MEDIUM|LOW",
      "file": "relative path e.g. src/auth/jwt.go"
    }
  ]
}
Rules: max one task per agent. polecat-debug always gets security/audit task.
polecat-review always reviews last. Be specific about file paths."""

    raw = call_do_inference(system, f"Meeting notes:\n{notes}")
    raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
    plan = json.loads(raw)
    emit("mayor", "CONVOY_CREATED",
         f"Convoy: {plan['convoy']} — {len(plan['tasks'])} tasks")
    return plan
```

### `run_agent(agent_name, task, target_file)`

This is the key function. It spawns a real Claude Code session in a tmux pane.

```python
def run_agent(agent_name, task, target_file):
    worktree = WORKTREES.get(agent_name)
    role = agent_name.replace("polecat-", "")
    done_file = f"/tmp/{agent_name}.done"
    if os.path.exists(done_file):
        os.remove(done_file)

    # Load gstack skill if available
    gstack_skill = ""
    skill_path = os.path.expanduser(
        f"~/.claude/skills/gstack/{role}/SKILL.md")
    if os.path.exists(skill_path):
        with open(skill_path) as f:
            gstack_skill = f"Methodology (gstack /{role}):\n{f.read()[:600]}\n\n"

    # Read existing file for context
    existing = ""
    full_path = os.path.join(worktree, target_file)
    if os.path.exists(full_path):
        with open(full_path) as f:
            existing = f.read()

    # Build the focused prompt for this Claude Code session
    prompt = f"""{gstack_skill}You are {agent_name}, a senior Go engineer in Gas Town.

Task: {task}
File: {target_file}
Working dir: {worktree}

Context (existing file):
{existing[:800] if existing else "File does not exist yet — create it."}

Instructions:
1. Write production-quality, idiomatic Go code for the task above.
2. Save to {target_file} in your working directory.
3. Run: git add . && git commit -m "{agent_name}: {task[:55]}"
4. Run: git push origin polecat/{role}
5. Run: echo DONE > /tmp/{agent_name}.done

No explanation needed. Just write the code and execute the git steps."""

    emit(agent_name, "AGENT_SPAWNED", f"{agent_name} online — worktree ready")
    time.sleep(0.3)
    emit(agent_name, "TASK_STARTED", f"Starting: {task}")

    # Create a tmux window named after the role
    subprocess.run(["tmux", "new-window", "-t", "gastown", "-n", role],
                   capture_output=True)
    # Launch Claude Code in that window
    safe_prompt = prompt.replace("'", "'\"'\"'")
    subprocess.run([
        "tmux", "send-keys", "-t", f"gastown:{role}",
        f"cd {worktree} && claude --print '{safe_prompt}'", "Enter"
    ])

    # Poll for completion signal (max 8 min)
    for _ in range(96):
        time.sleep(5)
        if os.path.exists(done_file):
            os.remove(done_file)
            # Read what was written
            written = ""
            if os.path.exists(full_path):
                with open(full_path) as f:
                    written = f.read()
            lines = len(written.splitlines()) if written else 0
            emit(agent_name, "CODE_WRITTEN",
                 f"{target_file} — {lines} lines committed",
                 diff=written[:500])
            time.sleep(1)
            emit(agent_name, "REVIEW_PASSED",
                 f"Pushed to branch polecat/{role}")
            return

    # Timeout — let dashboard show stuck state
    emit(agent_name, "AGENT_STUCK",
         f"{agent_name} timed out — use override button in dashboard")
```

### `open_pr(plan) -> str`
```python
def open_pr(plan):
    emit("mayor", "PR_OPENED", "Opening GitHub PR...")
    task_list = "\n".join(
        f"- {t['agent']}: {t['task']}" for t in plan["tasks"])
    result = subprocess.run([
        "gh", "pr", "create",
        "--title",  f"[Gas Town] {plan['convoy']}",
        "--body",   f"Multi-agent convoy via Gas Town\n\nStack: DO Inference + gstack methodology\n\nAgents:\n{task_list}",
        "--base",   "main",
        "--head",   "polecat/auth"
    ], cwd=os.path.expanduser("~/gastown/demo-repo"),
       capture_output=True, text=True)
    pr_url = result.stdout.strip()
    emit("mayor", "PR_OPENED", f"PR opened: {pr_url}", diff=pr_url)
    return pr_url
```

### `trigger_vercel_deploy()`
```python
def trigger_vercel_deploy():
    if not VERCEL_TOKEN:
        emit("vercel", "DEPLOY_SKIPPED", "VERCEL_TOKEN not set")
        return
    emit("vercel", "DEPLOY_STARTED", "Vercel triggered — building from main")
    headers = {"Authorization": f"Bearer {VERCEL_TOKEN}",
               "Content-Type": "application/json"}
    payload = {
        "name": VERCEL_PROJECT,
        "gitSource": {"type": "github", "ref": "main",
                      "repoId": os.environ.get("GITHUB_REPO_ID", "")},
        "target": "production"
    }
    resp = requests.post("https://api.vercel.com/v13/deployments",
                         headers=headers, json=payload)
    data = resp.json()
    deploy_id  = data.get("id", "")
    deploy_url = f"https://{data.get('url', VERCEL_PROJECT + '.vercel.app')}"
    emit("vercel", "DEPLOYMENT_TRIGGERED", f"Build started — {deploy_id}")

    # Poll until ready (max 3 min)
    for i in range(36):
        time.sleep(5)
        r = requests.get(
            f"https://api.vercel.com/v13/deployments/{deploy_id}",
            headers=headers).json()
        state = r.get("readyState", "BUILDING")
        if state == "READY":
            emit("vercel", "DEPLOYMENT_READY",
                 f"Live: {deploy_url}", diff=deploy_url)
            return
        if state == "ERROR":
            emit("vercel", "DEPLOYMENT_FAILED", "Build failed")
            return
```

### `run_convoy(notes)`
```python
def run_convoy(notes):
    emit("mayor", "AGENT_SPAWNED", "Mayor online — Gas Town active")
    plan = parse_meeting_notes(notes)

    threads = []
    for task_def in plan["tasks"]:
        t = threading.Thread(
            target=run_agent,
            args=(task_def["agent"], task_def["task"], task_def["file"])
        )
        threads.append(t)
        t.start()
        time.sleep(0.5)   # stagger spawning for visual effect

    for t in threads:
        t.join()

    pr_url = open_pr(plan)

    emit("mayor", "MERGED", "Refinery merged to main")
    trigger_vercel_deploy()
    emit("mayor", "CONVOY_COMPLETE", f"Done. Code shipped. Live on Vercel.")

if __name__ == "__main__":
    import sys
    # Accept notes from CLI args, or watch for /tmp/gastown-task file
    if len(sys.argv) > 1:
        run_convoy(" ".join(sys.argv[1:]))
    else:
        print("Watching for /tmp/gastown-task...")
        while True:
            if os.path.exists("/tmp/gastown-task"):
                with open("/tmp/gastown-task") as f:
                    notes = f.read().strip()
                os.remove("/tmp/gastown-task")
                run_convoy(notes)
            time.sleep(1)
```

---

## STEP 2: bridge/main.go

### go.mod:
```
module gastown-bridge
go 1.23
require github.com/google/uuid v1.6.0
```

### Structs:
```go
type Event struct {
    ID        string `json:"id"`
    Time      string `json:"time"`
    Agent     string `json:"agent"`
    AgentRole string `json:"agent_role"`
    Type      string `json:"type"`
    Text      string `json:"text"`
    Diff      string `json:"diff,omitempty"`
}

type AgentState struct {
    Name      string `json:"name"`
    Role      string `json:"role"`
    Status    string `json:"status"`
    LastTask  string `json:"last_task"`
    LastSeen  string `json:"last_seen"`
    DeployURL string `json:"deploy_url,omitempty"`
}
```

### Global state (sync.RWMutex protected):
- `clients map[chan Event]bool`
- `eventLog []Event` — ring buffer cap 200
- `agentStates map[string]AgentState`
- `convoyName string`

### Endpoints:

**GET /events** — SSE
- Headers: `Content-Type: text/event-stream`, `Cache-Control: no-cache`, `Access-Control-Allow-Origin: *`
- On connect: replay last 50 events oldest-first as `data: {json}\n\n`
- Stream new events via channel
- Clean up on r.Context().Done()

**POST /ingest** — receive events from orchestrator
- Validate `X-Bridge-Secret` header
- Decode JSON → Event
- Auto-fill ID (uuid) and Time if empty
- Update agentStates:
  - AGENT_SPAWNED, TASK_STARTED, CODE_WRITTEN, CSO_RUNNING → "working"
  - REVIEW_PASSED, AUDIT_DONE, CONVOY_COMPLETE → "done"
  - DEPLOYMENT_READY → set DeployURL from Diff field
  - AGENT_STUCK → "stuck"
- Broadcast to all SSE clients
- Add to ring buffer

**POST /task** — trigger orchestrator from browser
- Decode `{ "notes": "string" }`
- Write notes to `/tmp/gastown-task`
- Return 202 immediately
- Orchestrator file-watcher picks it up

**POST /demo/start** — scripted fallback sequence
Fire these events with delays (use goroutine + time.Sleep):
```
0s   mayor  CONVOY_CREATED    "Pre-Launch Sprint — 5 tasks, 5 agents"
0.5s auth   AGENT_SPAWNED     "polecat-auth online — worktree ready"
0.8s tests  AGENT_SPAWNED     "polecat-tests online — worktree ready"
1.1s debug  AGENT_SPAWNED     "polecat-debug online — gstack /cso loaded"
1.4s docs   AGENT_SPAWNED     "polecat-docs online — worktree ready"
1.7s review AGENT_SPAWNED     "polecat-review online — gstack /review loaded"
3s   auth   TASK_STARTED      "Examining server.go — adding JWT middleware"
4s   tests  TASK_STARTED      "Writing table-driven tests — 6 cases"
5s   debug  TASK_STARTED      "gstack /cso — OWASP Top 10 + STRIDE scan"
7s   auth   CODE_WRITTEN      "jwt.go — 84 lines committed, pushed polecat/auth"
8s   tests  CODE_WRITTEN      "auth_test.go — 6 tests committed, pushed"
10s  debug  AUDIT_DONE        "2 findings: token revocation, issuer claim"
12s  docs   CODE_WRITTEN      "README.md updated — auth section added"
14s  review REVIEW_PASSED     "gstack /review — no blocking issues found"
16s  mayor  PR_OPENED         "PR #43 opened — github.com/demo-repo/pull/43"
18s  mayor  MERGED            "Refinery merged to main — triggering Vercel"
19s  vercel DEPLOY_STARTED    "Vercel triggered — building from main"
28s  vercel DEPLOYMENT_READY  "Live: https://gastown-demo.vercel.app"
30s  mayor  CONVOY_COMPLETE   "Done. Code shipped. Live on Vercel."
```

**GET /state** — JSON snapshot
```json
{
  "agents": { "polecat-auth": { "status": "working" } },
  "convoy": "Pre-Launch Sprint",
  "event_count": 42,
  "deploy_url": "https://gastown-demo.vercel.app"
}
```

**GET /health** — `{"status":"ok","clients":N}`

### CORS: all handlers set `Access-Control-Allow-Origin: *`, handle OPTIONS preflight.

### Env vars: `PORT` (default 8080), `BRIDGE_SECRET` (default "gastown-demo-2026")

---

## STEP 3: dashboard/index.html

Copy `gastown-dashboard.html` as `dashboard/index.html`. Make these changes:

### Wire real SSE:
```javascript
const BRIDGE = 'http://YOUR_DROPLET_IP:8080';

const sse = new EventSource(`${BRIDGE}/events`);
sse.onmessage = (e) => {
    const ev = JSON.parse(e.data);
    handleRealEvent(ev);
};
sse.onerror = () => {
    document.querySelector('.live').style.color = 'var(--orange)';
};

function handleRealEvent(ev) {
    const role = ev.agent_role || ev.agent.replace('polecat-','');
    injectEvent(role, badgeClassFor(ev.type), ev.type, ev.text);
    updateAgentFromEvent(ev);

    if (ev.type === 'DEPLOYMENT_READY' && ev.diff) {
        const link = document.getElementById('vercel-live-link');
        link.href = ev.diff;
        link.classList.add('show');
    }
    if (ev.type === 'CONVOY_COMPLETE' || ev.type === 'MERGED') {
        const btn = document.getElementById('deploy-btn');
        btn.textContent = '▶ DEPLOY AGENTS';
        btn.classList.remove('running');
        btn.disabled = false;
    }
}

function badgeClassFor(type) {
    const m = {
        AGENT_SPAWNED:'b-sp', TASK_STARTED:'b-st', CODE_WRITTEN:'b-cw',
        REVIEW_PASSED:'b-rp', AUDIT_DONE:'b-cs', CSO_RUNNING:'b-cs',
        PR_OPENED:'b-pr', MERGED:'b-mg', CONVOY_CREATED:'b-mg',
        CONVOY_COMPLETE:'b-mg', DEPLOY_STARTED:'b-vb',
        DEPLOYMENT_READY:'b-vr', AGENT_STUCK:'b-rf',
    };
    return m[type] || 'b-st';
}

function updateAgentFromEvent(ev) {
    const role = ev.agent_role || ev.agent.replace('polecat-','');
    const stateMap = {
        AGENT_SPAWNED:'working', TASK_STARTED:'working',
        CODE_WRITTEN:'working', CSO_RUNNING:'working',
        AUDIT_DONE:'done', REVIEW_PASSED:'done',
        CONVOY_COMPLETE:'done', AGENT_STUCK:'stuck',
    };
    if (stateMap[ev.type]) setState(role, stateMap[ev.type], ev.text);
}
```

### Wire Deploy button → POST /task:
```javascript
async function triggerDemo() {
    const notes = document.getElementById('notes-input').value.trim();
    if (!notes) return;
    const btn = document.getElementById('deploy-btn');
    btn.textContent = '⏸ RUNNING...';
    btn.classList.add('running');
    btn.disabled = true;
    await fetch(`${BRIDGE}/task`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ notes })
    });
    // Events stream back via SSE — no need to wait here
}
```

### Remove all hardcoded placeholder events. Real events only from SSE.

---

## STEP 4: scripts/setup-worktrees.sh
```bash
#!/bin/bash
set -e
REPO=~/gastown/demo-repo
cd $REPO

git checkout main
for role in auth tests debug docs review; do
    git checkout -b polecat/$role 2>/dev/null || git checkout polecat/$role
    git checkout main
done

git push origin polecat/auth polecat/tests polecat/debug polecat/docs polecat/review --force

git worktree add ~/gastown/wt-auth    polecat/auth   2>/dev/null || true
git worktree add ~/gastown/wt-tests   polecat/tests  2>/dev/null || true
git worktree add ~/gastown/wt-debug   polecat/debug  2>/dev/null || true
git worktree add ~/gastown/wt-docs    polecat/docs   2>/dev/null || true
git worktree add ~/gastown/wt-review  polecat/review 2>/dev/null || true

echo "✓ Worktrees ready:"
git worktree list
```

---

## STEP 5: scripts/start.sh
```bash
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
```

---

## STEP 6: demo-repo starter code

`~/gastown/demo-repo/src/api/server.go`:
```go
package api

import (
    "encoding/json"
    "net/http"
)

// TODO: polecat-auth adding JWT middleware
// TODO: polecat-debug will audit this route
func NewRouter() *http.ServeMux {
    mux := http.NewServeMux()
    mux.HandleFunc("/health", healthHandler)
    mux.HandleFunc("/users", usersHandler)
    mux.HandleFunc("/search", searchHandler)
    return mux
}

func healthHandler(w http.ResponseWriter, r *http.Request) {
    json.NewEncoder(w).Encode(map[string]string{"status": "ok"})
}

func usersHandler(w http.ResponseWriter, r *http.Request) {
    // BUG: no auth check — polecat-auth is fixing this
    json.NewEncoder(w).Encode([]map[string]string{
        {"id": "1", "name": "Alice"},
    })
}

func searchHandler(w http.ResponseWriter, r *http.Request) {
    q := r.URL.Query().Get("q")
    // BUG: unsanitised input — polecat-debug will flag this
    json.NewEncoder(w).Encode(map[string]string{"query": q})
}
```

---

## ENV VARS (add to ~/.bashrc on droplet)
```bash
export MODEL_ACCESS_KEY="sk-do-YOUR_KEY"
export DO_INFERENCE_URL="https://inference.do-ai.run/v1"
export BRIDGE_SECRET="gastown-demo-2026"
export GITHUB_TOKEN="ghp_YOUR_TOKEN"
export VERCEL_TOKEN="YOUR_VERCEL_TOKEN"
export VERCEL_PROJECT="gastown-demo"
export GITHUB_REPO_ID=""   # gh api repos/OWNER/REPO --jq .id
```

---

## BUILD ORDER

1. `orchestrator.py` — test standalone first:
   ```bash
   python3 orchestrator.py "add JWT auth, write tests"
   # Events print to console. Bridge errors silently ignored.
   ```

2. `bridge/main.go` — build and test SSE:
   ```bash
   cd bridge && go build -o bridge . && ./bridge &
   # Terminal 1: curl -N localhost:8080/events
   # Terminal 2: curl -X POST localhost:8080/ingest \
   #   -H "X-Bridge-Secret: gastown-demo-2026" \
   #   -d '{"agent":"test","type":"TEST","text":"hello"}'
   # Event should appear in Terminal 1
   ```

3. Run both together — events flow orchestrator → bridge → SSE stream.

4. Wire `dashboard/index.html` to real SSE — open in browser, events appear live.

5. Test `/demo/start` fallback until it runs perfectly — this is your safety net.

6. Run full end-to-end 3 times. Fix every failure.

---

## DEMO NARRATIVE

> "Every standup produces notes. Notes become tickets. Tickets sit for a week.
> I'm collapsing that into 90 seconds."
>
> [paste audience notes → click Deploy Agents]
>
> "Each tmux pane is a real Claude Code session in its own git worktree.
> The debug agent is running gstack /cso — OWASP+STRIDE audit.
> You can verify that at github.com/garrytan/gstack right now, 96k stars.
> All models run on DigitalOcean Inference — one key, any model, no vendor lock-in."
>
> [PR opens on GitHub — click the link]
> [Vercel deploys — live URL appears in topbar]
>
> "One standup. Five agents. One live URL. 90 seconds."
