package main

import (
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"sync"
	"time"

	"github.com/google/uuid"
)

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

var (
	mu          sync.RWMutex
	clients     = make(map[chan Event]bool)
	eventLog    []Event
	agentStates = make(map[string]AgentState)
	convoyName  string
	deployURL   string
)

const maxLog = 200
const bridgeSecret = "gastown-demo-2026"

func getSecret() string {
	if s := os.Getenv("BRIDGE_SECRET"); s != "" {
		return s
	}
	return bridgeSecret
}

func addEvent(ev Event) {
	mu.Lock()
	if ev.ID == "" {
		ev.ID = uuid.New().String()
	}
	if ev.Time == "" {
		ev.Time = time.Now().Format("15:04:05")
	}
	if ev.AgentRole == "" && ev.Agent != "" {
		ev.AgentRole = ev.Agent
		for _, prefix := range []string{"polecat-"} {
			if len(ev.Agent) > len(prefix) && ev.Agent[:len(prefix)] == prefix {
				ev.AgentRole = ev.Agent[len(prefix):]
			}
		}
	}

	// Update agent state
	role := ev.AgentRole
	state := agentStates[ev.Agent]
	state.Name = ev.Agent
	state.Role = role
	state.LastTask = ev.Text
	state.LastSeen = ev.Time

	switch ev.Type {
	case "AGENT_SPAWNED", "TASK_STARTED", "CODE_WRITTEN", "CSO_RUNNING":
		state.Status = "working"
	case "REVIEW_PASSED", "AUDIT_DONE", "CONVOY_COMPLETE":
		state.Status = "done"
	case "DEPLOYMENT_READY":
		state.Status = "done"
		if ev.Diff != "" {
			deployURL = ev.Diff
			state.DeployURL = ev.Diff
		}
	case "AGENT_STUCK":
		state.Status = "stuck"
	}

	agentStates[ev.Agent] = state

	if ev.Type == "CONVOY_CREATED" {
		convoyName = ev.Text
	}

	eventLog = append(eventLog, ev)
	if len(eventLog) > maxLog {
		eventLog = eventLog[len(eventLog)-maxLog:]
	}

	// Broadcast to all clients
	for ch := range clients {
		select {
		case ch <- ev:
		default:
		}
	}
	mu.Unlock()
}

func corsHeaders(w http.ResponseWriter) {
	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.Header().Set("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
	w.Header().Set("Access-Control-Allow-Headers", "Content-Type, X-Bridge-Secret")
}

func handleSSE(w http.ResponseWriter, r *http.Request) {
	corsHeaders(w)
	if r.Method == http.MethodOptions {
		w.WriteHeader(http.StatusNoContent)
		return
	}

	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")

	flusher, ok := w.(http.Flusher)
	if !ok {
		http.Error(w, "streaming unsupported", http.StatusInternalServerError)
		return
	}

	ch := make(chan Event, 64)

	mu.Lock()
	clients[ch] = true
	// Replay last 50 events oldest-first
	replay := eventLog
	if len(replay) > 50 {
		replay = replay[len(replay)-50:]
	}
	mu.Unlock()

	for _, ev := range replay {
		data, _ := json.Marshal(ev)
		fmt.Fprintf(w, "data: %s\n\n", data)
	}
	flusher.Flush()

	ctx := r.Context()
	for {
		select {
		case <-ctx.Done():
			mu.Lock()
			delete(clients, ch)
			mu.Unlock()
			return
		case ev := <-ch:
			data, _ := json.Marshal(ev)
			fmt.Fprintf(w, "data: %s\n\n", data)
			flusher.Flush()
		}
	}
}

func handleIngest(w http.ResponseWriter, r *http.Request) {
	corsHeaders(w)
	if r.Method == http.MethodOptions {
		w.WriteHeader(http.StatusNoContent)
		return
	}
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	if r.Header.Get("X-Bridge-Secret") != getSecret() {
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return
	}

	var ev Event
	if err := json.NewDecoder(r.Body).Decode(&ev); err != nil {
		http.Error(w, "bad request", http.StatusBadRequest)
		return
	}

	addEvent(ev)
	w.WriteHeader(http.StatusAccepted)
}

func handleTask(w http.ResponseWriter, r *http.Request) {
	corsHeaders(w)
	if r.Method == http.MethodOptions {
		w.WriteHeader(http.StatusNoContent)
		return
	}
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	var body struct {
		Notes string `json:"notes"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil || body.Notes == "" {
		http.Error(w, "bad request", http.StatusBadRequest)
		return
	}

	if err := os.WriteFile("/tmp/gastown-task", []byte(body.Notes), 0644); err != nil {
		http.Error(w, "failed to write task", http.StatusInternalServerError)
		return
	}

	w.WriteHeader(http.StatusAccepted)
	json.NewEncoder(w).Encode(map[string]string{"status": "accepted"})
}

func handleDemoStart(w http.ResponseWriter, r *http.Request) {
	corsHeaders(w)
	if r.Method == http.MethodOptions {
		w.WriteHeader(http.StatusNoContent)
		return
	}
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	go runDemoSequence()
	w.WriteHeader(http.StatusAccepted)
	json.NewEncoder(w).Encode(map[string]string{"status": "demo started"})
}

func fire(delay time.Duration, agent, agentRole, evType, text string) {
	time.Sleep(delay)
	addEvent(Event{
		Agent:     agent,
		AgentRole: agentRole,
		Type:      evType,
		Text:      text,
	})
}

func runDemoSequence() {
	fire(0,     "mayor",          "mayor",  "CONVOY_CREATED",      "Pre-Launch Sprint — 5 tasks, 5 agents")
	fire(500*time.Millisecond,  "polecat-auth",   "auth",   "AGENT_SPAWNED",       "polecat-auth online — worktree ready")
	fire(800*time.Millisecond,  "polecat-tests",  "tests",  "AGENT_SPAWNED",       "polecat-tests online — worktree ready")
	fire(1100*time.Millisecond, "polecat-debug",  "debug",  "AGENT_SPAWNED",       "polecat-debug online — gstack /cso loaded")
	fire(1400*time.Millisecond, "polecat-docs",   "docs",   "AGENT_SPAWNED",       "polecat-docs online — worktree ready")
	fire(1700*time.Millisecond, "polecat-review", "review", "AGENT_SPAWNED",       "polecat-review online — gstack /review loaded")
	fire(3*time.Second,         "polecat-auth",   "auth",   "TASK_STARTED",        "Examining server.go — adding JWT middleware")
	fire(4*time.Second,         "polecat-tests",  "tests",  "TASK_STARTED",        "Writing table-driven tests — 6 cases")
	fire(5*time.Second,         "polecat-debug",  "debug",  "TASK_STARTED",        "gstack /cso — OWASP Top 10 + STRIDE scan")
	fire(7*time.Second,         "polecat-auth",   "auth",   "CODE_WRITTEN",        "jwt.go — 84 lines committed, pushed polecat/auth")
	fire(8*time.Second,         "polecat-tests",  "tests",  "CODE_WRITTEN",        "auth_test.go — 6 tests committed, pushed")
	fire(10*time.Second,        "polecat-debug",  "debug",  "AUDIT_DONE",          "2 findings: token revocation, issuer claim")
	fire(12*time.Second,        "polecat-docs",   "docs",   "CODE_WRITTEN",        "README.md updated — auth section added")
	fire(14*time.Second,        "polecat-review", "review", "REVIEW_PASSED",       "gstack /review — no blocking issues found")
	fire(16*time.Second,        "mayor",          "mayor",  "PR_OPENED",           "PR #43 opened — github.com/demo-repo/pull/43")
	fire(18*time.Second,        "mayor",          "mayor",  "MERGED",              "Refinery merged to main — triggering Vercel")
	fire(19*time.Second,        "vercel",         "vercel", "DEPLOY_STARTED",      "Vercel triggered — building from main")

	time.Sleep(28 * time.Second)
	addEvent(Event{
		Agent:     "vercel",
		AgentRole: "vercel",
		Type:      "DEPLOYMENT_READY",
		Text:      "Live: https://gastown-demo.vercel.app",
		Diff:      "https://gastown-demo.vercel.app",
	})

	fire(30*time.Second, "mayor", "mayor", "CONVOY_COMPLETE", "Done. Code shipped. Live on Vercel.")
}

func handleState(w http.ResponseWriter, r *http.Request) {
	corsHeaders(w)
	if r.Method == http.MethodOptions {
		w.WriteHeader(http.StatusNoContent)
		return
	}

	mu.RLock()
	agents := make(map[string]AgentState)
	for k, v := range agentStates {
		agents[k] = v
	}
	convoy := convoyName
	count := len(eventLog)
	url := deployURL
	mu.RUnlock()

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"agents":       agents,
		"convoy":       convoy,
		"event_count":  count,
		"deploy_url":   url,
	})
}

func handleHealth(w http.ResponseWriter, r *http.Request) {
	corsHeaders(w)
	mu.RLock()
	n := len(clients)
	mu.RUnlock()
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{"status": "ok", "clients": n})
}

func main() {
	port := os.Getenv("PORT")
	if port == "" {
		port = "8080"
	}

	mux := http.NewServeMux()
	mux.HandleFunc("/events",     handleSSE)
	mux.HandleFunc("/ingest",     handleIngest)
	mux.HandleFunc("/task",       handleTask)
	mux.HandleFunc("/demo/start", handleDemoStart)
	mux.HandleFunc("/state",      handleState)
	mux.HandleFunc("/health",     handleHealth)

	log.Printf("Gas Town bridge listening on :%s", port)
	if err := http.ListenAndServe(":"+port, mux); err != nil {
		log.Fatal(err)
	}
}
