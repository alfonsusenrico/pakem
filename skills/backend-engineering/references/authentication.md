# Authentication

Status: Active
Applies to: projects with the `deployed` or `multi-user` capability
Last reviewed: 2026-10-07
Review by: 2027-04-07
Sources: owner interview 2026-10-07; OWASP Session Management and JWT cheat sheets

## Requirements for any mechanism

- Expiry on every session or token; refresh or re-login defined.
- Revocation: logout and password change invalidate sessions (for JWT, a token-version
  or deny-list check).
- CSRF protection whenever cookies authenticate requests (SameSite plus a CSRF token
  or origin check).
- Constant-time comparison for passwords, tokens, signatures, and API keys; hash
  passwords with a slow algorithm (argon2id or bcrypt) and API keys with a fast hash
  plus prefix.
- No long-lived tokens in storage readable by page scripts (`localStorage`,
  `sessionStorage`).
- Re-authentication for destructive or financial admin actions.
- Rate limiting and lockout signals on login and recovery endpoints.

## Defaults

- Own web apps: server-side session in an HttpOnly, Secure, SameSite cookie.
- APIs consumed by separate frontends or mobile apps: short-lived JWT bearer access
  tokens with token-version revocation and a refresh flow.
- Service-to-service: short-lived signed tokens or per-service API keys with scopes.
