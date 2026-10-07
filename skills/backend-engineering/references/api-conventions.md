# API Conventions

Status: Active
Applies to: HTTP APIs
Last reviewed: 2026-10-07
Review by: 2027-04-07
Sources: RFC 9457 (Problem Details for HTTP APIs); owner route convention (2026-10)

## Routes

- `/api/v1/<plural-resource>[/<id>][/<sub-resource-or-action>]`, kebab-case, nouns;
  no verb prefixes such as `get-` or `create-`. Actions that are not CRUD use a
  sub-path noun or verb (`/orders/{id}/cancel`).
- Version in the path (`/v1`); breaking changes need a new version or a
  compatibility window.

## Success responses

- Return the resource itself (`200`/`201`), not a wrapper.
- Lists: `{"items": [...], "meta": {"total": n, "page": p, "page_size": s}}` (or
  cursor fields). `204` for successful operations with no body.

## Errors (RFC 9457)

`Content-Type: application/problem+json`

```json
{
  "type": "https://example.com/problems/insufficient-quota",
  "title": "Quota exceeded",
  "status": 409,
  "code": "quota_exceeded",
  "detail": "Human-readable explanation in the UI language when shown to users.",
  "request_id": "01J...",
  "errors": [{"field": "seats", "code": "too_large", "message": "..."}]
}
```

- `code` is stable and machine-readable; clients branch on `code`, never on text.
- `request_id` matches the `X-Request-ID` response header.
- Never include stack traces, SQL, provider error text, or secrets.
- `429` and `503` include `Retry-After` when retrying can succeed.

## Documentation

Every endpoint documents its success and error statuses in OpenAPI. With the
`api-consumers` capability the generated OpenAPI is the contract.
