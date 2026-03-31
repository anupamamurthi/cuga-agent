# API Design

When designing REST APIs:

- Resources are nouns, plural (e.g. `/users`, `/rate-limits`)
- Use standard HTTP verbs: GET (read), POST (create), PUT (replace), PATCH (update), DELETE
- Return appropriate status codes: 200, 201, 400, 401, 403, 404, 409, 422, 429, 500
- Version the API in the path: `/v1/...`
- Use snake_case for JSON field names
- Paginate list endpoints: `limit` + `cursor` or `offset`
- Document rate-limit headers: `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`
- Error responses must include: `code`, `message`, `request_id`
