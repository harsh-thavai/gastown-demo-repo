package tests

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"testing"

	"todo-auth-service/config"
	"todo-auth-service/handlers"
	"todo-auth-service/models"
	"todo-auth-service/routes"
	"todo-auth-service/services"

	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"gorm.io/driver/sqlite"
	"gorm.io/gorm"
)

var (
	testRouter *gin.Engine
	testDB     *gorm.DB
	testJWTKey []byte
)

func TestMain(m *testing.M) {
	gin.SetMode(gin.TestMode)

	var err error
	testDB, err = gorm.Open(sqlite.Open(":memory:"), &gorm.Config{})
	if err != nil {
		panic("failed to connect to test database: " + err.Error())
	}

	err = testDB.AutoMigrate(&models.User{}, &models.Todo{})
	if err != nil {
		panic("failed to migrate test database: " + err.Error())
	}

	testJWTKey = []byte("test-secret-key-for-integration-tests")
	cfg := &config.Config{
		JWTSecret: string(testJWTKey),
	}

	authService := services.NewAuthService(testDB, cfg)
	todoService := services.NewTodoService(testDB)
	authHandler := handlers.NewAuthHandler(authService)
	todoHandler := handlers.NewTodoHandler(todoService)
	healthHandler := handlers.NewHealthHandler()

	testRouter = routes.SetupRouter(authHandler, todoHandler, healthHandler, cfg)

	code := m.Run()

	sqlDB, _ := testDB.DB()
	if sqlDB != nil {
		sqlDB.Close()
	}

	os.Exit(code)
}

// Helper function to create a test user and return their token
func registerAndLogin(t *testing.T) (string, string) {
	t.Helper()

	registerBody := map[string]string{
		"username": "testuser",
		"password": "password123",
	}
	bodyBytes, _ := json.Marshal(registerBody)
	req, _ := http.NewRequest(http.MethodPost, "/api/auth/register", bytes.NewReader(bodyBytes))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	testRouter.ServeHTTP(w, req)
	require.Equal(t, http.StatusCreated, w.Code)

	loginBody := map[string]string{
		"username": "testuser",
		"password": "password123",
	}
	bodyBytes, _ = json.Marshal(loginBody)
	req, _ = http.NewRequest(http.MethodPost, "/api/auth/login", bytes.NewReader(bodyBytes))
	req.Header.Set("Content-Type", "application/json")
	w = httptest.NewRecorder()
	testRouter.ServeHTTP(w, req)

	var response map[string]interface{}
	err := json.Unmarshal(w.Body.Bytes(), &response)
	require.NoError(t, err)

	token, ok := response["token"].(string)
	require.True(t, ok)
	require.NotEmpty(t, token)

	userID, ok := response["user_id"].(string)
	require.True(t, ok)

	return token, userID
}

// ---------- AUTH TESTS ----------

func TestRegister_Success(t *testing.T) {
	testDB.Exec("DELETE FROM users")
	registerBody := map[string]string{
		"username": "newuser",
		"password": "securepassword",
	}
	bodyBytes, _ := json.Marshal(registerBody)
	req, _ := http.NewRequest(http.MethodPost, "/api/auth/register", bytes.NewReader(bodyBytes))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	testRouter.ServeHTTP(w, req)

	assert.Equal(t, http.StatusCreated, w.Code)

	var response map[string]interface{}
	err := json.Unmarshal(w.Body.Bytes(), &response)
	require.NoError(t, err)
	assert.NotEmpty(t, response["token"])
	assert.NotEmpty(t, response["user_id"])
}

func TestRegister_DuplicateUsername(t *testing.T) {
	testDB.Exec("DELETE FROM users")
	registerBody := map[string]string{
		"username": "dupuser",
		"password": "password123",
	}
	bodyBytes, _ := json.Marshal(registerBody)
	req, _ := http.NewRequest(http.MethodPost, "/api/auth/register", bytes.NewReader(bodyBytes))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	testRouter.ServeHTTP(w, req)
	assert.Equal(t, http.StatusCreated, w.Code)

	w = httptest.NewRecorder()
	req, _ = http.NewRequest(http.MethodPost, "/api/auth/register", bytes.NewReader(bodyBytes))
	req.Header.Set("Content-Type", "application/json")
	testRouter.ServeHTTP(w, req)
	assert.Equal(t, http.StatusConflict, w.Code)
}

func TestRegister_MissingFields(t *testing.T) {
	registerBody := map[string]string{
		"username": "incomplete",
	}
	bodyBytes, _ := json.Marshal(registerBody)
	req, _ := http.NewRequest(http.MethodPost, "/api/auth/register", bytes.NewReader(bodyBytes))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	testRouter.ServeHTTP(w, req)

	assert.Equal(t, http.StatusBadRequest, w.Code)
}

func TestLogin_Success(t *testing.T) {
	testDB.Exec("DELETE FROM users")
	testDB.Exec("DELETE FROM todos")
	token, _ := registerAndLogin(t)
	assert.NotEmpty(t, token)
}

func TestLogin_InvalidCredentials(t *testing.T) {
	testDB.Exec("DELETE FROM users")
	testDB.Exec("DELETE FROM todos")
	_ = registerAndLogin(t)

	loginBody := map[string]string{
		"username": "testuser",
		"password": "wrongpassword",
	}
	bodyBytes, _ := json.Marshal(loginBody)
	req, _ := http.NewRequest(http.MethodPost, "/api/auth/login", bytes.NewReader(bodyBytes))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	testRouter.ServeHTTP(w, req)

	assert.Equal(t, http.StatusUnauthorized, w.Code)
}

func TestLogin_NonexistentUser(t *testing.T) {
	loginBody := map[string]string{
		"username": "ghostuser",
		"password": "password123",
	}
	bodyBytes, _ := json.Marshal(loginBody)
	req, _ := http.NewRequest(http.MethodPost, "/api/auth/login", bytes.NewReader(bodyBytes))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	testRouter.ServeHTTP(w, req)

	assert.Equal(t, http.StatusUnauthorized, w.Code)
}

// ---------- HEALTH CHECK TEST ----------

func TestHealthCheck(t *testing.T) {
	req, _ := http.NewRequest(http.MethodGet, "/health", nil)
	w := httptest.NewRecorder()
	testRouter.ServeHTTP(w, req)

	assert.Equal(t, http.StatusOK, w.Code)

	var response map[string]interface{}
	err := json.Unmarshal(w.Body.Bytes(), &response)
	require.NoError(t, err)
	assert.Equal(t, "ok", response["status"])
}

// ---------- TODO TESTS ----------

func TestCreateTodo_Success(t *testing.T) {
	testDB.Exec("DELETE FROM users")
	testDB.Exec("DELETE FROM todos")
	token, _ := registerAndLogin(t)

	todoBody := map[string]string{
		"title":       "Buy groceries",
		"description": "Milk, eggs, bread",
	}
	bodyBytes, _ := json.Marshal(todoBody)
	req, _ := http.NewRequest(http.MethodPost, "/api/todos", bytes.NewReader(bodyBytes))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+token)
	w := httptest.NewRecorder()
	testRouter.ServeHTTP(w, req)

	assert.Equal(t, http.StatusCreated, w.Code)

	var response map[string]interface{}
	err := json.Unmarshal(w.Body.Bytes(), &response)
	require.NoError(t, err)
	assert.Equal(t, "Buy groceries", response["title"])
	assert.Equal(t, "Milk, eggs, bread", response["description"])
	assert.NotEmpty(t, response["id"])
	assert.NotEmpty(t, response["user_id"])
	assert.Equal(t, false, response["completed"])
}

func TestCreateTodo_Unauthenticated(t *testing.T) {
	todoBody := map[string]string{
		"title": "No auth",
	}
	bodyBytes, _ := json.Marshal(todoBody)
	req, _ := http.NewRequest(http.MethodPost, "/api/todos", bytes.NewReader(bodyBytes))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	testRouter.ServeHTTP(w, req)

	assert.Equal(t, http.StatusUnauthorized, w.Code)
}

func TestCreateTodo_MissingTitle(t *testing.T) {
	testDB.Exec("DELETE FROM users")
	testDB.Exec("DELETE FROM todos")
	token, _ := registerAndLogin(t)

	todoBody := map[string]string{
		"description": "Missing title",
	}
	bodyBytes, _ := json.Marshal(todoBody)
	req, _ := http.NewRequest(http.MethodPost, "/api/todos", bytes.NewReader(bodyBytes))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+token)
	w := httptest.NewRecorder()
	testRouter.ServeHTTP(w, req)

	assert.Equal(t, http.StatusBadRequest, w.Code)
}

func TestGetTodos_Success(t *testing.T) {
	testDB.Exec("DELETE FROM users")
	testDB.Exec("DELETE FROM todos")
	token, _ := registerAndLogin(t)

	todoBody := map[string]string{
		"title": "Todo 1",
	}
	bodyBytes, _ := json.Marshal(todoBody)
	req, _ := http.NewRequest(http.MethodPost, "/api/todos", bytes.NewReader(bodyBytes))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+token)
	w := httptest.NewRecorder()
	testRouter.ServeHTTP(w, req)
	assert.Equal(t, http.StatusCreated, w.Code)

	todoBody["title"] = "Todo 2"
	bodyBytes, _ = json.Marshal(todoBody)
	req, _ = http.NewRequest(http.MethodPost, "/api/todos", bytes.NewReader(bodyBytes))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+token)
	w = httptest.NewRecorder()
	testRouter.ServeHTTP(w, req)
	assert.Equal(t, http.StatusCreated, w.Code)

	req, _ = http.NewRequest(http.MethodGet, "/api/todos", nil)
	req.Header.Set("Authorization", "Bearer "+token)
	w = httptest.NewRecorder()
	testRouter.ServeHTTP(w, req)

	assert.Equal(t, http.StatusOK, w.Code)

	var response []interface{}
	err := json.Unmarshal(w.Body.Bytes(), &response)
	require.NoError(t, err)
	assert.Len(t, response, 2)
}

func TestGetTodoByID_Success(t *testing.T) {
	testDB.Exec("DELETE FROM users")
	testDB.Exec("DELETE FROM todos")
	token, _ := registerAndLogin(t)

	todoBody := map[string]string{
		"title":       "Specific Todo",
		"description": "Details",
	}
	bodyBytes, _ := json.Marshal(todoBody)
	req, _ := http.NewRequest(http.MethodPost, "/api/todos", bytes.NewReader(bodyBytes))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+token)
	w := httptest.NewRecorder()
	testRouter.ServeHTTP(w, req)
	assert.Equal(t, http.StatusCreated, w.Code)

	var created map[string]interface{}
	err := json.Unmarshal(w.Body.Bytes(), &created)
	require.NoError(t, err)
	id := created["id"].(string)

	req, _ = http.NewRequest(http.MethodGet, "/api/todos/"+id, nil)
	req.Header.Set("Authorization", "Bearer "+token)
	w = httptest.NewRecorder()
	testRouter.ServeHTTP(w, req)

	assert.Equal(t, http.StatusOK, w.Code)

	var response map[string]interface{}
	err = json.Unmarshal(w.Body.Bytes(), &response)
	require.NoError(t, err)
	assert.Equal(t, "Specific Todo", response["title"])
	assert.Equal(t, "Details", response["description"])
}

func TestGetTodoByID_NotFound(t *testing.T) {
	testDB.Exec("DELETE FROM users")
	testDB.Exec("DELETE FROM todos")
	token, _ := registerAndLogin(t)

	req, _ := http.NewRequest(http.MethodGet, "/api/todos/nonexistent-id", nil)
	req.Header.Set("Authorization", "Bearer "+token)
	w := httptest.NewRecorder()
	testRouter.ServeHTTP(w, req)

	assert.Equal(t, http.StatusNotFound, w.Code)
}

func TestGetTodoByID_UnauthorizedAccess(t *testing.T) {
	testDB.Exec("DELETE FROM users")
	testDB.Exec("DELETE FROM todos")
	token, _ := registerAndLogin(t)

	todoBody := map[string]string{
		"title": "User 1 Todo",
	}
	bodyBytes, _ := json.Marshal(todoBody)
	req, _ := http.NewRequest(http.MethodPost, "/api/todos", bytes.NewReader(bodyBytes))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+token)
	w := httptest.NewRecorder()
	testRouter.ServeHTTP(w, req)
	assert.Equal(t, http.StatusCreated, w.Code)

	var created map[string]interface{}
	err := json.Unmarshal(w.Body.Bytes(), &created)
	require.NoError(t, err)
	id := created["id"].(string)

	registerBody := map[string]string{
		"username": "user2",
		"password": "password123",
	}
	bodyBytes, _ = json.Marshal(registerBody)
	req, _ = http.NewRequest(http.MethodPost, "/api/auth/register", bytes.NewReader(bodyBytes))
	req.Header.Set("Content-Type", "application/json")
	w = httptest.NewRecorder()
	testRouter.ServeHTTP(w, req)
	assert.Equal(t, http.StatusCreated, w.Code)

	loginBody := map[string]string{
		"username": "user2",
		"password": "password123",
	}
	bodyBytes, _ = json.Marshal(loginBody)
	req, _ = http.NewRequest(http.MethodPost, "/api/auth/login", bytes.NewReader(bodyBytes))
	req.Header.Set("Content-Type", "application/json")
	w = httptest.NewRecorder()
	testRouter.ServeHTTP(w, req)

	var loginResp map[string]interface{}
	err = json.Unmarshal(w.Body.Bytes(), &loginResp)
	require.NoError(t, err)
	user2Token := loginResp["token"].(string)

	req, _ = http.NewRequest(http.MethodGet, "/api/todos/"+id, nil)
	req.Header.Set("Authorization", "Bearer "+user2Token)
	w = httptest.NewRecorder()
	testRouter.ServeHTTP(w, req)

	assert.Equal(t, http.StatusForbidden, w.Code)
}

func TestUpdateTodo_Success(t *testing.T) {
	testDB.Exec("DELETE FROM users")
	testDB.Exec("DELETE FROM todos")
	token, _ := registerAndLogin(t)

	todoBody := map[string]string{
		"title": "Original Title",
	}
	bodyBytes, _ := json.Marshal(todoBody)
	req, _ := http.NewRequest(http.MethodPost, "/api/todos", bytes.NewReader(bodyBytes))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+token)
	w := httptest.NewRecorder()
	testRouter.ServeHTTP(w, req)
	assert.Equal(t, http.StatusCreated, w.Code)

	var created map[string]interface{}
	err := json.Unmarshal(w.Body.Bytes(), &created)
	require.NoError(t, err)
	id := created["id"].(string)

	updateBody := map[string]interface{}{
		"title":       "Updated Title",
		"description": "Updated Description",
		"completed":   true,
	}
	bodyBytes, _ = json.Marshal(updateBody)
	req, _ = http.NewRequest(http.MethodPut, "/api/todos/"+id, bytes.NewReader(bodyBytes))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+token)
	w = httptest.NewRecorder()
	testRouter.ServeHTTP(w, req)

	assert.Equal(t, http.StatusOK, w.Code)

	var response map[string]interface{}
	err = json.Unmarshal(w.Body.Bytes(), &response)
	require.NoError(t, err)
	assert.Equal(t, "Updated Title", response["title"])
	assert.Equal(t, "Updated Description", response["description"])
	assert.Equal(t, true, response["completed"])
}

func TestUpdateTodo_NotFound(t *testing.T) {
	testDB.Exec("DELETE FROM users")
	testDB.Exec("DELETE FROM todos")
	token, _ := registerAndLogin(t)

	updateBody := map[string]string{
		"title": "Nothing",
	}
	bodyBytes, _ := json.Marshal(updateBody)
	req, _ := http.NewRequest(http.MethodPut, "/api/todos/nonexistent-id", bytes.NewReader(bodyBytes))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+token)
	w := httptest.NewRecorder()
	testRouter.ServeHTTP(w, req)

	assert.Equal(t, http.StatusNotFound, w.Code)
}

func TestDeleteTodo_Success(t *testing.T) {
	testDB.Exec("DELETE FROM users")
	testDB.Exec("DELETE FROM todos")
	token, _ := registerAndLogin(t)

	todoBody := map[string]string{
		"title": "To be deleted",
	}
	bodyBytes, _ := json.Marshal(todoBody)
	req, _ := http.NewRequest(http.MethodPost, "/api/todos", bytes.NewReader(bodyBytes))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+token)
	w := httptest.NewRecorder()
	testRouter.ServeHTTP(w, req)
	assert.Equal(t, http.StatusCreated, w.Code)

	var created map[string]interface{}
	err := json.Unmarshal(w.Body.Bytes(), &created)
	require.NoError(t, err)
	id := created["id"].(string)

	req, _ = http.NewRequest(http.MethodDelete, "/api/todos/"+id, nil)
	req.Header.Set("Authorization", "Bearer "+token)
	w = httptest.NewRecorder()
	testRouter.ServeHTTP(w, req)

	assert.Equal(t, http.StatusOK, w.Code)

	req, _ = http.NewRequest(http.MethodGet, "/api/todos/"+id, nil)
	req.Header.Set("Authorization", "Bearer "+token)
	w = httptest.NewRecorder()
	testRouter.ServeHTTP(w, req)
	assert.Equal(t, http.StatusNotFound, w.Code)
}

func TestDeleteTodo_NotFound(t *testing.T) {
	testDB.Exec("DELETE FROM users")
	testDB.Exec("DELETE FROM todos")
	token, _ := registerAndLogin(t)

	req, _ := http.NewRequest(http.MethodDelete, "/api/todos/nonexistent-id", nil)
	req.Header.Set("Authorization", "Bearer "+token)
	w := httptest.NewRecorder()
	testRouter.ServeHTTP(w, req)

	assert.Equal(t, http.StatusNotFound, w.Code)
}

func TestDeleteTodo_UnauthorizedAccess(t *testing.T) {
	testDB.Exec("DELETE FROM users")
	testDB.Exec("DELETE FROM todos")
	token, _ := registerAndLogin(t)

	todoBody := map[string]string{
		"title": "User 1 Todo",
	}
	bodyBytes, _ := json.Marshal(todoBody)
	req, _ := http.NewRequest(http.MethodPost, "/api/todos", bytes.NewReader(bodyBytes))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+token)
	w := httptest.NewRecorder()
	testRouter.ServeHTTP(w, req)
	assert.Equal(t, http.StatusCreated, w.Code)

	var created map[string]interface{}
	err := json.Unmarshal(w.Body.Bytes(), &created)
	require.NoError(t, err)
	id := created["id"].(string)

	registerBody := map[string]string{
		"username": "user2",
		"password": "password123",
	}
	bodyBytes, _ = json.Marshal(registerBody)
	req, _ = http.NewRequest(http.MethodPost, "/api/auth/register", bytes.NewReader(bodyBytes))
	req.Header.Set("Content-Type", "application/json")
	w = httptest.NewRecorder()
	testRouter.ServeHTTP(w, req)

	loginBody := map[string]string{
		"username": "user2",
		"password": "password123",
	}
	bodyBytes, _ = json.Marshal(loginBody)
	req, _ = http.NewRequest(http.MethodPost, "/api/auth/login", bytes.NewReader(bodyBytes))
	req.Header.Set("Content-Type", "application/json")
	w = httptest.NewRecorder()
	testRouter.ServeHTTP(w, req)

	var loginResp map[string]interface{}
	err = json.Unmarshal(w.Body.Bytes(), &loginResp)
	require.NoError(t, err)
	user2Token := loginResp["token"].(string)

	req, _ = http.NewRequest(http.MethodDelete, "/api/todos/"+id, nil)
	req.Header.Set("Authorization", "Bearer "+user2Token)
	w = httptest.NewRecorder()
	testRouter.ServeHTTP(w, req)

	assert.Equal(t, http.StatusForbidden, w.Code)
}