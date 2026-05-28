package tests

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/stretchr/testify/assert"
)

func TestCreateUser(t *testing.T) {
	router := setupRouter()

	payload := map[string]interface{}{
		"name":     "John Doe",
		"email":    "john@example.com",
		"password": "securePass123",
	}

	body, _ := json.Marshal(payload)

	req := httptest.NewRequest(http.MethodPost, "/users", bytes.NewBuffer(body))
	req.Header.Set("Content-Type", "application/json")

	w := httptest.NewRecorder()
	router.ServeHTTP(w, req)

	assert.Equal(t, http.StatusCreated, w.Code)

	var response map[string]interface{}
	err := json.Unmarshal(w.Body.Bytes(), &response)
	assert.NoError(t, err)

	assert.NotNil(t, response["id"])
	assert.Equal(t, "John Doe", response["name"])
	assert.Equal(t, "john@example.com", response["email"])
}

func TestGetUser(t *testing.T) {
	router := setupRouter()

	createPayload := map[string]interface{}{
		"name":     "Jane Doe",
		"email":    "jane@example.com",
		"password": "securePass456",
	}
	createBody, _ := json.Marshal(createPayload)
	createReq := httptest.NewRequest(http.MethodPost, "/users", bytes.NewBuffer(createBody))
	createReq.Header.Set("Content-Type", "application/json")
	createW := httptest.NewRecorder()
	router.ServeHTTP(createW, createReq)

	var createdUser map[string]interface{}
	json.Unmarshal(createW.Body.Bytes(), &createdUser)
	userID := createdUser["id"]

	req := httptest.NewRequest(http.MethodGet, "/users/"+userID.(string), nil)
	w := httptest.NewRecorder()
	router.ServeHTTP(w, req)

	assert.Equal(t, http.StatusOK, w.Code)

	var response map[string]interface{}
	err := json.Unmarshal(w.Body.Bytes(), &response)
	assert.NoError(t, err)

	assert.Equal(t, userID, response["id"])
	assert.Equal(t, "Jane Doe", response["name"])
	assert.Equal(t, "jane@example.com", response["email"])
}

func TestUpdateUser(t *testing.T) {
	router := setupRouter()

	createPayload := map[string]interface{}{
		"name":     "Bob Smith",
		"email":    "bob@example.com",
		"password": "password789",
	}
	createBody, _ := json.Marshal(createPayload)
	createReq := httptest.NewRequest(http.MethodPost, "/users", bytes.NewBuffer(createBody))
	createReq.Header.Set("Content-Type", "application/json")
	createW := httptest.NewRecorder()
	router.ServeHTTP(createW, createReq)

	var createdUser map[string]interface{}
	json.Unmarshal(createW.Body.Bytes(), &createdUser)
	userID := createdUser["id"]

	updatePayload := map[string]interface{}{
		"name":   "Robert Smith",
		"active": false,
	}
	updateBody, _ := json.Marshal(updatePayload)

	req := httptest.NewRequest(http.MethodPut, "/users/"+userID.(string), bytes.NewBuffer(updateBody))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	router.ServeHTTP(w, req)

	assert.Equal(t, http.StatusOK, w.Code)

	var response map[string]interface{}
	err := json.Unmarshal(w.Body.Bytes(), &response)
	assert.NoError(t, err)

	assert.Equal(t, userID, response["id"])
	assert.Equal(t, "Robert Smith", response["name"])
	assert.Equal(t, "bob@example.com", response["email"])
	assert.Equal(t, false, response["active"])
}

func TestDeleteUser(t *testing.T) {
	router := setupRouter()

	createPayload := map[string]interface{}{
		"name":     "Alice Johnson",
		"email":    "alice@example.com",
		"password": "deletePass000",
	}
	createBody, _ := json.Marshal(createPayload)
	createReq := httptest.NewRequest(http.MethodPost, "/users", bytes.NewBuffer(createBody))
	createReq.Header.Set("Content-Type", "application/json")
	createW := httptest.NewRecorder()
	router.ServeHTTP(createW, createReq)

	var createdUser map[string]interface{}
	json.Unmarshal(createW.Body.Bytes(), &createdUser)
	userID := createdUser["id"]

	req := httptest.NewRequest(http.MethodDelete, "/users/"+userID.(string), nil)
	w := httptest.NewRecorder()
	router.ServeHTTP(w, req)

	assert.Equal(t, http.StatusNoContent, w.Code)

	getReq := httptest.NewRequest(http.MethodGet, "/users/"+userID.(string), nil)
	getW := httptest.NewRecorder()
	router.ServeHTTP(getW, getReq)

	assert.Equal(t, http.StatusNotFound, getW.Code)
}

func TestListUsers(t *testing.T) {
	router := setupRouter()

	createPayload1 := map[string]interface{}{
		"name":     "User One",
		"email":    "one@example.com",
		"password": "password1",
	}
	body1, _ := json.Marshal(createPayload1)
	req1 := httptest.NewRequest(http.MethodPost, "/users", bytes.NewBuffer(body1))
	req1.Header.Set("Content-Type", "application/json")
	w1 := httptest.NewRecorder()
	router.ServeHTTP(w1, req1)

	createPayload2 := map[string]interface{}{
		"name":     "User Two",
		"email":    "two@example.com",
		"password": "password2",
	}
	body2, _ := json.Marshal(createPayload2)
	req2 := httptest.NewRequest(http.MethodPost, "/users", bytes.NewBuffer(body2))
	req2.Header.Set("Content-Type", "application/json")
	w2 := httptest.NewRecorder()
	router.ServeHTTP(w2, req2)

	req := httptest.NewRequest(http.MethodGet, "/users", nil)
	w := httptest.NewRecorder()
	router.ServeHTTP(w, req)

	assert.Equal(t, http.StatusOK, w.Code)

	var response []map[string]interface{}
	err := json.Unmarshal(w.Body.Bytes(), &response)
	assert.NoError(t, err)

	assert.GreaterOrEqual(t, len(response), 2)
}

func TestCreateUserValidation(t *testing.T) {
	router := setupRouter()

	tests := []struct {
		name       string
		payload    map[string]interface{}
		wantStatus int
	}{
		{
			name: "missing email",
			payload: map[string]interface{}{
				"name":     "No Email",
				"password": "password123",
			},
			wantStatus: http.StatusBadRequest,
		},
		{
			name: "missing password",
			payload: map[string]interface{}{
				"name":  "No Password",
				"email": "nopass@example.com",
			},
			wantStatus: http.StatusBadRequest,
		},
		{
			name: "invalid email format",
			payload: map[string]interface{}{
				"name":     "Bad Email",
				"email":    "not-an-email",
				"password": "password123",
			},
			wantStatus: http.StatusBadRequest,
		},
		{
			name: "short password",
			payload: map[string]interface{}{
				"name":     "Short Pass",
				"email":    "short@example.com",
				"password": "123",
			},
			wantStatus: http.StatusBadRequest,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			body, _ := json.Marshal(tt.payload)
			req := httptest.NewRequest(http.MethodPost, "/users", bytes.NewBuffer(body))
			req.Header.Set("Content-Type", "application/json")
			w := httptest.NewRecorder()
			router.ServeHTTP(w, req)

			assert.Equal(t, tt.wantStatus, w.Code)
		})
	}
}

func TestGetUserNotFound(t *testing.T) {
	router := setupRouter()

	req := httptest.NewRequest(http.MethodGet, "/users/nonexistent-id", nil)
	w := httptest.NewRecorder()
	router.ServeHTTP(w, req)

	assert.Equal(t, http.StatusNotFound, w.Code)
}

func TestUpdateUserNotFound(t *testing.T) {
	router := setupRouter()

	updatePayload := map[string]interface{}{
		"name": "Ghost User",
	}
	body, _ := json.Marshal(updatePayload)
	req := httptest.NewRequest(http.MethodPut, "/users/nonexistent-id", bytes.NewBuffer(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	router.ServeHTTP(w, req)

	assert.Equal(t, http.StatusNotFound, w.Code)
}

func TestDeleteUserNotFound(t *testing.T) {
	router := setupRouter()

	req := httptest.NewRequest(http.MethodDelete, "/users/nonexistent-id", nil)
	w := httptest.NewRecorder()
	router.ServeHTTP(w, req)

	assert.Equal(t, http.StatusNotFound, w.Code)
}