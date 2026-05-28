# Polecat

A high-performance URL shortener built with Go and Redis.

## Features

- Shorten long URLs into compact, shareable links
- Redirect with proper HTTP status codes (301 Moved Permanently)
- Configurable rate limiting per client IP
- Persistent storage via Redis
- Comprehensive metrics and health check endpoint
- Dockerized deployment

## Prerequisites

- Go 1.21 or later
- Docker and Docker Compose (optional)
- Redis 7.x

## Quick Start

### Local Development

```bash
# Clone the repository
git clone https://github.com/your-org/polecat.git
cd polecat

# Install dependencies
go mod download

# Start Redis (if not running)
docker run -d -p 6379:6379 redis:7-alpine

# Run the application
go run cmd/server/main.go
```

The server starts on `http://localhost:8080` by default.

### Using Docker Compose

```bash
docker compose up --build
```

## Configuration

Configuration is managed via environment variables:

| Variable | Description | Default |
|----------|-------------|---------|
| `SERVER_PORT` | HTTP server port | `8080` |
| `REDIS_ADDR` | Redis connection address | `localhost:6379` |
| `REDIS_PASSWORD` | Redis password | (empty) |
| `REDIS_DB` | Redis database number | `0` |
| `BASE_URL` | Base URL for shortened links | `http://localhost:8080` |
| `RATE_LIMIT_REQUESTS` | Number of requests allowed in window | `100` |
| `RATE_LIMIT_WINDOW` | Rate limit window duration | `1m` |
| `SHORTCODE_LENGTH` | Length of generated shortcodes | `8` |
| `LOG_LEVEL` | Logging level (debug, info, warn, error) | `info` |

## API Reference

### Shorten URL

Create a new shortened URL.

```http
POST /api/v1/shorten
Content-Type: application/json

{
  "url": "https://example.com/very/long/url/that/needs/shortening",
  "custom_code": "my-custom-code"
}
```

**Parameters:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `url` | string | Yes | The long URL to shorten |
| `custom_code` | string | No | Custom shortcode (alphanumeric, 4-20 chars) |

**Success Response (201 Created):**

```json
{
  "short_url": "http://localhost:8080/abc12345",
  "shortcode": "abc12345",
  "original_url": "https://example.com/very/long/url/that/needs/shortening",
  "created_at": "2024-12-01T10:30:00Z"
}
```

**Error Responses:**

- `400 Bad Request` — Invalid URL or custom code format
- `409 Conflict` — Custom code already in use
- `429 Too Many Requests` — Rate limit exceeded

### Redirect

Follow a shortened URL.

```http
GET /{shortcode}
```

**Response:**
- `301 Moved Permanently` — Redirects to the original URL with `Location` header
- `404 Not Found` — Shortcode does not exist

### Health Check

Check service health and dependencies.

```http
GET /health
```

**Success Response (200 OK):**

```json
{
  "status": "healthy",
  "redis": "connected",
  "uptime": "2h45m12s",
  "version": "1.0.0"
}
```

### Metrics

Retrieve application metrics in Prometheus format.

```http
GET /metrics
```

**Response:** Plain text Prometheus exposition format with counters, histograms, and gauges.

## Rate Limiting

Polecat implements token bucket rate limiting per client IP address to protect against abuse.

### How It Works

- Each request to `/api/v1/shorten` consumes one token from the client's bucket
- When the bucket is empty, subsequent requests receive `429 Too Many Requests`
- Buckets automatically refill over the configured window

### Rate Limit Configuration

The default configuration allows **100 requests per minute** per client IP.

```bash
# Allow 500 requests per 5 minutes
export RATE_LIMIT_REQUESTS=500
export RATE_LIMIT_WINDOW=5m
```

### Rate Limit Response

When the rate limit is exceeded, the API returns:

```http
HTTP/1.1 429 Too Many Requests
Retry-After: 45
Content-Type: application/json

{
  "error": "rate_limit_exceeded",
  "message": "Too many requests. Please try again later.",
  "retry_after_seconds": 45
}
```

The `Retry-After` header indicates the number of seconds to wait before retrying.

### Bypass for Trusted Proxies

If deploying behind a reverse proxy (Nginx, HAProxy, Cloudflare), configure trusted proxy ranges to extract the real client IP:

```bash
export TRUSTED_PROXIES="10.0.0.0/8,172.16.0.0/12"
```

## Architecture

```
       ┌──────────┐      ┌──────────┐      ┌──────────┐
       │  Client  │      │  Polecat │      │  Redis   │
       └────┬─────┘      └────┬─────┘      └────┬─────┘
            │   POST /shorten │                  │
            ├────────────────>│                  │
            │                 │  SET shortcode   │
            │                 ├─────────────────>│
            │                 │       OK         │
            │   201 Created   │<─────────────────┤
            │<────────────────┤                  │
            │                 │                  │
            │  GET /{code}    │                  │
            ├────────────────>│                  │
            │                 │ GET original_url │
            │                 ├─────────────────>│
            │                 │  original_url    │
            │  301 Redirect   │<─────────────────┤
            │<────────────────┤                  │
```

## Testing

```bash
# Run unit tests
go test ./... -v

# Run with race detection
go test -race ./...

# Run integration tests (requires Redis)
REDIS_ADDR=localhost:6379 go test -tags=integration ./...
```

## Project Structure

```
.
├── cmd/
│   └── server/
│       └── main.go           # Application entrypoint
├── internal/
│   ├── api/
│   │   ├── handler.go        # HTTP handlers
│   │   ├── middleware.go     # Rate limiting, logging middleware
│   │   └── router.go         # Route definitions
│   ├── config/
│   │   └── config.go         # Configuration loading
│   ├── ratelimit/
│   │   └── limiter.go        # Token bucket rate limiter
│   ├── shortener/
│   │   ├── service.go        # URL shortening logic
│   │   └── generator.go      # Shortcode generation
│   └── storage/
│       ├── redis.go          # Redis client and operations
│       └── memory.go         # In-memory store for testing
├── docker-compose.yml
├── Dockerfile
├── go.mod
├── go.sum
└── README.md
```

## Deployment

### Docker

```dockerfile
FROM golang:1.21-alpine AS builder
WORKDIR /app
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -o /polecat cmd/server/main.go

FROM alpine:3.19
RUN apk add --no-cache ca-certificates
COPY --from=builder /polecat /usr/local/bin/polecat
EXPOSE 8080
ENTRYPOINT ["polecat"]
```

### Kubernetes

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: polecat
spec:
  replicas: 3
  selector:
    matchLabels:
      app: polecat
  template:
    metadata:
      labels:
        app: polecat
    spec:
      containers:
      - name: polecat
        image: polecat:latest
        ports:
        - containerPort: 8080
        env:
        - name: REDIS_ADDR
          value: "redis-service:6379"
        - name: BASE_URL
          value: "https://short.example.com"
```

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## License

MIT License. See [LICENSE](LICENSE) for details.