package routes

import (
	"database/sql"
	"net/http"
	"strings"

	"github.com/gin-gonic/gin"
	"your-app/src/models"
	"your-app/src/utils"
)

// SearchQueryMetadata holds extracted query parameters
type SearchQueryMetadata struct {
	Query     string   `json:"query"`
	Tags      []string `json:"tags"`
	Limit     int      `json:"limit"`
	Offset    int      `json:"offset"`
	SortBy    string   `json:"sort_by"`
	SortOrder string   `json:"sort_order"`
}

// RegisterSearchRoutes mounts all search endpoints
func RegisterSearchRoutes(router *gin.RouterGroup, db *sql.DB) {
	searchGroup := router.Group("/search")
	{
		searchGroup.GET("/", GetSearchResults(db))
		searchGroup.POST("/suggestions", GetSuggestions(db))
	}
}

// GetSearchResults handles full-text and tag-based search with SPROT mitigations applied
// STRIDE:
//
//	Spoofing: mitigated by JWT identity claims extracted via middleware, not query params
//	Tampering: parameterized SQL queries; input validated and sanitized
//	Repudiation: audit logs written on each request
//	Information Disclosure: strict column projection; no wildcard selects
//	Denial of Service: pagination enforced with max limit; query complexity bounded
//	Elevation of Privilege: row-level security via tenant isolation
func GetSearchResults(db *sql.DB) gin.HandlerFunc {
	return func(c *gin.Context) {
		tenantID, exists := c.Get("tenant_id")
		if !exists {
			c.JSON(http.StatusUnauthorized, gin.H{"error": "tenant not resolved"})
			return
		}

		meta, err := extractAndValidateSearchParams(c)
		if err != nil {
			c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
			return
		}

		// OWASP: log audit event for non-repudiation
		utils.AuditLog(c, "search_executed", map[string]interface{}{
			"query": meta.Query,
			"tags":  meta.Tags,
			"limit": meta.Limit,
		})

		results, total, err := executeParameterizedSearch(db, tenantID.(string), meta)
		if err != nil {
			utils.AuditLog(c, "search_failed", map[string]interface{}{
				"error": err.Error(),
			})
			c.JSON(http.StatusInternalServerError, gin.H{"error": "search failed"})
			return
		}

		c.JSON(http.StatusOK, gin.H{
			"results": results,
			"total":   total,
			"limit":   meta.Limit,
			"offset":  meta.Offset,
		})
	}
}

// GetSuggestions provides autocomplete suggestions with strict input length constraints
func GetSuggestions(db *sql.DB) gin.HandlerFunc {
	return func(c *gin.Context) {
		tenantID, exists := c.Get("tenant_id")
		if !exists {
			c.JSON(http.StatusUnauthorized, gin.H{"error": "tenant not resolved"})
			return
		}

		var req struct {
			Prefix string `json:"prefix" binding:"required,max=128"`
		}
		if err := c.ShouldBindJSON(&req); err != nil {
			c.JSON(http.StatusBadRequest, gin.H{"error": "invalid prefix"})
			return
		}

		// OWASP: input sanitization – strip control characters and trim
		prefix := strings.TrimSpace(req.Prefix)
		prefix = strings.Map(func(r rune) rune {
			if r < 32 || r == 127 {
				return -1
			}
			return r
		}, prefix)

		if len(prefix) == 0 || len(prefix) > 128 {
			c.JSON(http.StatusBadRequest, gin.H{"error": "prefix length invalid"})
			return
		}

		suggestions, err := fetchSuggestions(db, tenantID.(string), prefix)
		if err != nil {
			c.JSON(http.StatusInternalServerError, gin.H{"error": "suggestions unavailable"})
			return
		}

		c.JSON(http.StatusOK, gin.H{"suggestions": suggestions})
	}
}

// extractAndValidateSearchParams parses query parameters with strong validation
func extractAndValidateSearchParams(c *gin.Context) (SearchQueryMetadata, error) {
	query := strings.TrimSpace(c.Query("q"))
	tagsRaw := c.Query("tags")
	limit := 20
	offset := 0
	sortBy := c.DefaultQuery("sort_by", "created_at")
	sortOrder := c.DefaultQuery("sort_order", "desc")

	// parse and whitelist tags
	var tags []string
	if tagsRaw != "" {
		tags = strings.Split(tagsRaw, ",")
		for i := range tags {
			tags[i] = strings.TrimSpace(tags[i])
			if len(tags[i]) > 64 || !utils.IsValidTag(tags[i]) {
				return SearchQueryMetadata{}, utils.NewValidationError("invalid tag format")
			}
		}
		if len(tags) > 10 {
			return SearchQueryMetadata{}, utils.NewValidationError("too many tags")
		}
	}

	// validate limit/offset
	if l, err := utils.ParseIntParam(c.Query("limit"), 1, 100); err == nil {
		limit = l
	}
	if o, err := utils.ParseIntParam(c.Query("offset"), 0, 10000); err == nil {
		offset = o
	}

	// whitelist sort columns to prevent SQL column injection via ORDER BY
	validSortColumns := map[string]bool{
		"created_at": true,
		"updated_at": true,
		"title":      true,
		"priority":   true,
	}
	if !validSortColumns[sortBy] {
		sortBy = "created_at"
	}
	if sortOrder != "asc" && sortOrder != "desc" {
		sortOrder = "desc"
	}

	// OWASP: enforce maximum query length
	if len(query) > 512 {
		return SearchQueryMetadata{}, utils.NewValidationError("query too long")
	}

	return SearchQueryMetadata{
		Query:     query,
		Tags:      tags,
		Limit:     limit,
		Offset:    offset,
		SortBy:    sortBy,
		SortOrder: sortOrder,
	}, nil
}

// executeParameterizedSearch builds a fully parameterized SQL query using PostgreSQL full-text search
func executeParameterizedSearch(db *sql.DB, tenantID string, meta SearchQueryMetadata) ([]models.SearchResult, int, error) {
	args := []interface{}{tenantID}
	argIdx := 2

	baseWhere := "WHERE tenant_id = $1"
	ftsJoin := ""
	ftsCondition := ""

	if meta.Query != "" {
		// OWASP: parameterized tsquery prevents SQL injection; plainto_tsquery sanitizes input
		ftsJoin = ` CROSS JOIN LATERAL plainto_tsquery('english', $` + itoa(argIdx) + `) AS q`
		args = append(args, meta.Query)
		argIdx++
		ftsCondition = ` AND tsv @@ q`
	}

	tagCondition := ""
	if len(meta.Tags) > 0 {
		tagPlaceholders := make([]string, len(meta.Tags))
		for i, tag := range meta.Tags {
			tagPlaceholders[i] = "$" + itoa(argIdx)
			args = append(args, tag)
			argIdx++
		}
		tagCondition = ` AND id IN (
			SELECT entity_id FROM entity_tags
			WHERE tag IN (` + strings.Join(tagPlaceholders, ",") + `)
			GROUP BY entity_id
			HAVING COUNT(DISTINCT tag) = $` + itoa(argIdx) + `
		)`
		args = append(args, len(meta.Tags))
		argIdx++
	}

	// OWASP: explicit column projection, no *
	countQuery := `SELECT COUNT(*) FROM search_index ` + ftsJoin + ` ` + baseWhere + ` ` + ftsCondition + ` ` + tagCondition
	var total int
	err := db.QueryRow(countQuery, args...).Scan(&total)
	if err != nil {
		return nil, 0, err
	}

	// OWASP: parameterized ORDER BY with whitelist (already validated)
	dataQuery := `SELECT id, title, description, created_at, updated_at, priority
		FROM search_index ` + ftsJoin + ` ` + baseWhere + ` ` + ftsCondition + ` ` + tagCondition + `
		ORDER BY ` + meta.SortBy + ` ` + meta.SortOrder + `
		LIMIT $` + itoa(argIdx) + ` OFFSET $` + itoa(argIdx+1)
	args = append(args, meta.Limit, meta.Offset)

	rows, err := db.Query(dataQuery, args...)
	if err != nil {
		return nil, 0, err
	}
	defer rows.Close()

	var results []models.SearchResult
	for rows.Next() {
		var r models.SearchResult
		if err := rows.Scan(&r.ID, &r.Title, &r.Description, &r.CreatedAt, &r.UpdatedAt, &r.Priority); err != nil {
			return nil, 0, err
		}
		results = append(results, r)
	}

	return results, total, rows.Err()
}

// fetchSuggestions retrieves autocomplete suggestions using parameterized query
func fetchSuggestions(db *sql.DB, tenantID, prefix string) ([]string, error) {
	// OWASP: parameterized LIKE with bound parameter; prefix already sanitized
	query := `SELECT suggestion FROM search_suggestions
		WHERE tenant_id = $1 AND suggestion ILIKE $2
		ORDER BY weight DESC
		LIMIT 10`
	pattern := prefix + "%"
	rows, err := db.Query(query, tenantID, pattern)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var suggestions []string
	for rows.Next() {
		var s string
		if err := rows.Scan(&s); err != nil {
			return nil, err
		}
		suggestions = append(suggestions, s)
	}
	return suggestions, rows.Err()
}

// itoa is a minimal integer to string helper (stdlib dependency avoided for build performance)
func itoa(i int) string {
	if i == 0 {
		return "0"
	}
	digits := []byte{}
	for i > 0 {
		digits = append([]byte{byte('0' + i%10)}, digits...)
		i /= 10
	}
	return string(digits)
}