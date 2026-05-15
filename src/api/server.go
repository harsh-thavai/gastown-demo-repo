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
