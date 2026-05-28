# Security Audit: Auth and Todo Modules

## Executive Summary
- **Date**: 2025-04-07
- **Auditor**: Senior Engineer / Security Specialist
- **Scope**: `auth` and `todo` modules (Go services)
- **Frameworks Applied**: OWASP Top 10 (2021), STRIDE (Microsoft Threat Model)
- **Overall Risk**: Medium. Several high-risk findings related to input validation, token management, and logging require immediate remediation before production deployment.

## Module 1: Authentication Service (`auth`)

### OWASP Top 10 Assessment

#### A01:2021 – Broken Access Control
**Status**: Requires Fix
**Findings**:
- **JWT Token Validation**: The `ValidateToken` method in `auth/jwt.go` does not verify token `jti` against a revocation list. A revoked token can be reused until its natural expiry.
- **Role Claim Validation**: In `auth/middleware.go`, authorization middleware checks `claims["role"]` but does not perform a type assertion. A maliciously crafted token with `"role": true` could bypass boolean checks if the comparison logic is weak (`if role == "admin"`).
- **Missing Ownership Check**: No mechanism exists to validate that a user requesting a resource is the owner. Any authenticated user can call internal endpoints.

**Recommendations**:
- Implement a Redis-backed JWT blacklist checked on every request.
- Add strict type assertion for role claims: `roleStr, ok := claims["role"].(string)`.
- Enforce resource-level ownership validation via context-propagated user IDs.

#### A02:2021 – Cryptographic Failures
**Status**: Requires Fix
**Findings**:
- **Weak Password Hashing**: `auth/password.go` uses `bcrypt` with cost factor 5 (default in the `golang.org/x/crypto/bcrypt` library when not explicitly set). Modern best practices require minimum cost 12.
- **RSA Key Size**: `auth/rsa.go` generates 2048-bit RSA keys. NIST and OWASP recommend RSA-3072 or higher for long-lived tokens.
- **Static Secret**: `auth/config.go` reads `JWT_SECRET` from environment but falls back to a hardcoded string `"dev-secret-change-me"` if unset. This default is dangerous in containerized environments where the env var might be missed.

**Recommendations**:
- Set `bcrypt.DefaultCost = 12` explicitly.
- Migrate to Ed25519 for signing (faster, smaller, stronger than RSA-2048) or increase RSA key to 4096 bits.
- Remove hardcoded fallback; log fatal error and exit if `JWT_SECRET` is empty.

#### A03:2021 – Injection
**Status**: Informational
**Findings**:
- **Log Injection**: In `auth/handler.go`, login failure logs include `username` directly via `log.Printf("Failed login for %s", username)`. An attacker can inject newline characters (`%0a`) to forge log entries.
- **NoSQL Injection**: Not applicable (no NoSQL database used).
- **SQL Injection**: Not applicable (database layer uses parameterized queries via `pgx`).

**Recommendations**:
- Sanitize all user-supplied input before logging. Strip `\n` and `\r` characters from username.
- Implement a structured logging library (`zerolog`, `slog`) that escapes strings automatically.

#### A04:2021 – Insecure Design
**Status**: Informational
**Findings**:
- **Rate Limiting Absent**: `auth/login.go` has no rate-limiting middleware. Brute-force attacks are possible.
- **No Account Lockout**: After 5 consecutive failed login attempts, the account is not temporarily locked.
- **Password Policy Weakness**: `auth/validation.go` only enforces a minimum 8-character length. No complexity requirements (uppercase, digits, specials) or breach-list check (e.g., Have I Been Pwned API).

**Recommendations**:
- Add token-bucket or sliding-window rate limiter on `/login` endpoint (e.g., 5 attempts per minute per IP).
- Implement progressive delay (`time.Sleep` with exponential backoff) on failed attempts.
- Enforce NIST SP 800-63B password guidelines (min 8 chars, no complexity rules, but mandatory blacklist checking).

#### A05:2021 – Security Misconfiguration
**Status**: OK
**Findings**:
- `auth/server.go` sets `ReadTimeout: 15s, WriteTimeout: 15s, IdleTimeout: 60s` — good.
- TLS is enforced via middleware redirect in production config.
- Debug endpoints are disabled when `ENV=production`.

**Recommendations**: None at this time.

#### A06:2021 – Vulnerable and Outdated Components
**Status**: Requires Fix
**Findings**:
- `go.mod` has `github.com/golang-jwt/jwt/v5 v5.2.0`. CVE-2024-51744 affects versions < 5.2.1 (unvalidated `aud` claim).
- `github.com/lib/pq` is imported but unused; should be removed to reduce attack surface.

**Recommendations**:
- Run `go get -u github.com/golang-jwt/jwt/v5` to pull latest patch.
- Run `go mod tidy` and audit with `govulncheck ./...`.

#### A07:2021 – Identification and Authentication Failures
**Status**: Requires Fix
**Findings**:
- **Token Storage**: The refresh token is stored in an `httpOnly` cookie, but the `Secure` flag is not set in development mode conditional logic. This is controlled by an if-else that checks the environment improperly: `if env != "production" { cookie.Secure = false }`. This will set `Secure=false` in staging/QA if `env` is anything other than `"production"` exactly.
- **Session Fixation**: The service does not rotate session/refresh tokens after password change or privilege escalation. An attacker with a stolen refresh token retains access indefinitely.
- **No MFA Support**: No endpoint or architecture exists for multi-factor authentication.

**Recommendations**:
- Always set `Secure=true` and `SameSite=Strict` attributes on cookies, overriding only if a non-HTTPS dev flag is explicitly set.
- Invalidate all refresh tokens for a user upon password change (`DELETE FROM refresh_tokens WHERE user_id = $1`).
- Design `/api/auth/mfa/enroll` and `/verify` endpoints for future implementation.

#### A08:2021 – Software and Data Integrity Failures
**Status**: OK
**Findings**:
- CI/CD pipeline performs checksum verification on go modules (`go mod verify`).
- Docker images are pinned by SHA256 digest.

**Recommendations**: None.

#### A09:2021 – Security Logging and Monitoring Failures
**Status**: Requires Fix
**Findings**:
- Successful logins are logged at `INFO` level, but include the full JWT token in the log message: `log.Infof("User %s logged in, token: %s", username, token)`.
- Logging lacks request IDs, making it impossible to trace a request across services.
- Failed authentication is logged as `INFO` instead of `WARN`.

**Recommendations**:
- Never log credentials or tokens. Use a masked value: `token[:8] + "..."`.
- Propagate `X-Request-ID` header and include it in every log line.
- Elevate log level for auth failures to `WARN` and send to a SIEM.

#### A10:2021 – Server-Side Request Forgery (SSRF)
**Status**: OK
**Findings**:
- The auth service does not make any outbound HTTP requests to user-supplied URLs.

**Recommendations**: None.

### STRIDE Threat Model

| Threat Category | Threat Description | Affected Component | Mitigation |
|-----------------|--------------------|--------------------|------------|
| **Spoofing** | User impersonation via stolen JWT | `auth/middleware.go` | Implement token binding (TLS fingerprint) or short-lived access tokens (5 min) with refresh rotation. |
| **Tampering** | JWT payload modification | `auth/jwt.go` | Signature verification already exists, but `alg=none` check is missing. Add explicit check: `if _, ok := token.Method.(*jwt.SigningMethodHMAC); !ok { return error }`. |
| **Repudiation** | User denies performing admin action | `auth/audit_log.go` | Ensure all mutating operations (delete, update) are logged to an append-only audit table with user ID, timestamp, and action. |
| **Information Disclosure** | Password reflection in error messages | `auth/handler.go` | The error message `"Invalid password for user %s"` reveals valid usernames. Use generic: `"Invalid credentials"`. |
| **Denial of Service** | JWT bomb (large headers) | `auth/middleware.go` | Limit HTTP header size (`MaxHeaderBytes = 1 << 20`), and reject JWTs larger than 8KB. |
| **Elevation of Privilege** | User upgrades role via API | `auth/roles.go` | The `PUT /api/users/:id/role` endpoint only checks if the requestor is `admin`, but does not validate a whitelist of target roles. An admin could set their own role to `superadmin` via a crafted request. Validate the allowed transition set. |

## Module 2: Todo Service (`todo`)

### OWASP Top 10 Assessment

#### A01:2021 – Broken Access Control
**Status**: Critical
**Findings**:
- **Indirect Object Reference (IDOR)**: In `todo/handler.go`, `GET /api/todos/:id` fetches the todo by ID directly from the database without checking `todo.UserID == authenticatedUser.ID`. User A can view, update, or delete User B's todo by guessing the UUID.
- **Missing Auth Middleware**: The `todo/router.go` applies auth middleware to the `/api/todos` group, but a second group `/api/public/todos` exists for internal sharing and is accidentally exposed externally without authentication in the API gateway config.

**Recommendations**:
- Add a database query filter: `SELECT * FROM todos WHERE id = $1 AND user_id = $2`.
- Remove or secure the public group; if for internal use, add mutual TLS or a shared secret.

#### A02:2021 – Cryptographic Failures
**Status**: OK
**Findings**: No cryptographic operations in this service.

**Recommendations**: None.

#### A03:2021 – Injection
**Status**: Requires Fix
**Findings**:
- **SQL Injection via Sorting**: In `todo/repository.go`, the `List` function accepts a `sortBy` parameter and concatenates it directly into the query: `fmt.Sprintf("SELECT * FROM todos ORDER BY %s", sortBy)`. Parameterized queries cannot protect ORDER BY clauses.
- **CQL Injection**: Not applicable.
- **Command Injection**: Not applicable.

**Recommendations**:
- Whitelist allowed columns: `allowedSortColumns := map[string]bool{"created_at": true, "title": true, "priority": true}`. Reject any key not in this map.
- Escaping is not sufficient; strict whitelist is the only safe approach.

#### A04:2021 – Insecure Design
**Status**: Informational
**Findings**:
- **Mass Assignment**: The `Update` endpoint (`todo/handler.go`) binds the entire request body to a `Todo` struct, including fields like `UserID` and `IsAdmin`. A user could set `IsAdmin: true` in the JSON body, and if the ORM doesn't filter, it will be persisted.

**Recommendations**:
- Use a dedicated input DTO: `type UpdateTodoInput struct { Title string; Completed bool; Priority string }`.
- Explicitly map only those fields to the database model. Never use `db.Update(&todoFromRequest)`.

#### A05:2021 – Security Misconfiguration
**Status**: OK
**Findings**: Proper timeouts set, verbose error pages disabled.

**Recommendations**: None.

#### A06:2021 – Vulnerable and Outdated Components
**Status**: OK (same as auth service; upgrade JWT).

#### A07:2021 – Identification and Authentication Failures
**Status**: Informational
**Findings**:
- The todo service trusts the JWT extracted from the `Authorization` header blindly. It does not verify the token signature (assumes a sidecar or gateway already verified). If the gateway is misconfigured to pass requests directly, token forgery is possible.

**Recommendations**:
- Implement JWT verification internally as a secondary check. Use the same public key as the auth service. This follows the defense-in-depth principle.

#### A08:2021 – Software and Data Integrity Failures
**Status**: OK.

#### A09:2021 – Security Logging and Monitoring Failures
**Status**: Requires Fix
**Findings**:
- Todo deletion is not logged. If a user claims their data was deleted maliciously, there is no audit trail.
- Sensitive data (the entire todo object, potentially containing PII in the title) is logged in development mode at `DEBUG` level.

**Recommendations**:
- Log deletion events with `user_id`, `todo_id`, `timestamp` to an audit log.
- Switch to `DEBUG` or `TRACE` level and ensure those levels are suppressed in production.

#### A10:2021 – SSRF
**Status**: OK.

### STRIDE Threat Model

| Threat Category | Threat Description | Affected Component | Mitigation |
|-----------------|--------------------|--------------------|------------|
| **Spoofing** | Token replay with altered audience claim (`aud`) | `todo/middleware_optional.go` | Validate `aud` claim equals "todo-service". |
| **Tampering** | User tampers with `completed` timestamp to falsify history | `todo/model.go` | The `completed_at` timestamp is set server-side only. No user input is accepted for this field. Already mitigated. |
| **Repudiation** | User denies creating a todo | `todo/repository.go` | `created_at` and `created_by` are populated from the authenticated context. Strong non-repudiation achieved. |
| **Information Disclosure** | Enumeration attack on todo IDs | `todo/handler.go` | If an IDOR vulnerability exists, an attacker can enumerate valid UUIDs by checking response codes (404 vs. 403). Always return `404 Not Found` when the ID does not exist OR belongs to another user. |
| **Denial of Service** | Large payload attack | `todo/server.go` | Add `http.MaxBytesReader` to limit request body to 1MB. |
| **Elevation of Privilege** | User claims to be admin via JWT and accesses admin stats endpoint | `todo/admin_handler.go` | The admin endpoint checks `role == "admin"` but does not verify the role claim's issuer. An attacker could craft a JWT with the same structure from a different issuer. Validate `iss` claim against expected auth service URL. |

## Cross-Cutting Concerns
1. **Credentials in Config**: Both services use `os.Getenv` which is acceptable, but consider using a vault solution (HashiCorp Vault, AWS Secrets Manager) for production.
2. **Container Security**: Dockerfiles run as `root`. Add `USER 1000:1000` directive in final stage.
3. **CORS Policy**: `todo/cors.go` sets `Access-Control-Allow-Origin: *`. This permits any website to make authenticated requests using the user's cookies if the user is logged in via browser. Restrict to known frontend origins.
4. **Health Endpoint Exposure**: `/healthz` endpoints return database status. In `todo/health.go`, a successful DB ping returns `{"status": "ok", "db": "connected"}`. If the database is down, it returns the full connection error string, potentially leaking internal IPs. Sanitize error messages to generic "service unavailable".

## Remediation Priority Matrix

| Priority | Finding | Module | Effort | Risk |
|----------|---------|--------|--------|------|
| **P0 - Critical** | IDOR in Todo List/Update/Delete | todo | 2 hours | High |
| **P0 - Critical** | SQL Injection via sorting parameter | todo | 1 hour | High |
| **P0 - Critical** | Token logging in login handler | auth | 0.5 hours | High |
| **P0 - Critical** | `alg=none` bypass not checked | auth | 0.5 hours | High |
| **P1 - High** | Mass assignment vulnerability | todo | 3 hours | Medium |
| **P1 - High** | Weak bcrypt cost factor | auth | 0.5 hours | Medium |
| **P1 - High** | No JWT revocation capability | auth | 8 hours (new feature) | Medium |
| **P2 - Medium** | Rate limiting missing | auth | 4 hours | Low |
| **P2 - Medium** | Credential enumeration via error messages | auth | 0.5 hours | Low |
| **P2 - Medium** | Insecure CORS wildcard | todo | 0.5 hours | Low |
| **P3 - Low** | Public endpoint unintentionally exposed | todo | 1 hour | Low |
| **P3 - Low** | Docker runs as root | both | 0.5 hours | Low |

## Conclusion
The `auth` and `todo` services exhibit a typical set of issues common to early-stage Go microservices. The most severe vulnerabilities are IDOR and SQL Injection, which directly enable data breach and manipulation. The authentication module has a solid foundation but lacks token lifecycle management and robust brute-force protection. After applying the recommended mitigations, conduct a secondary penetration test before promoting to production.