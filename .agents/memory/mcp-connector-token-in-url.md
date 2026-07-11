---
name: Exposing an MCP connector alongside a REST API with per-user tokens
description: How to add a Claude-compatible remote MCP connector to an app that already has token-based REST auth, without a new auth mechanism.
---

When a user wants "Claude to call this API directly" in addition to an existing REST API, add a minimal hand-rolled MCP server (Streamable HTTP transport: single POST route accepting JSON-RPC 2.0 `initialize`/`tools/list`/`tools/call`/notifications, responding with plain JSON — no SSE/session needed for simple tool calls). No SDK required.

**Auth pattern — CORRECTED (2026-07-11): do NOT embed the token in the connector URL path.** An earlier version of this note recommended `/mcp/{token}`; in practice Claude's client connects successfully (shows "connected") but never calls `tools/list` — token-in-URL is not a scheme Claude's MCP client recognizes as auth, so tool discovery silently fails with no visible error. Confirmed via Anthropic's own connector docs: request-header/static-bearer auth is gated behind a beta admin-only feature, and the auth type actually "supported out of the box" for remote MCP connectors is OAuth 2.0 with Dynamic Client Registration (DCR) + PKCE.

**Working fix:** implement a minimal OAuth 2.0 authorization server in the same process: `/.well-known/oauth-authorization-server` + `/.well-known/oauth-protected-resource` (metadata), `POST /oauth/register` (DCR, no auth, public client, no secret), `GET/POST /oauth/authorize` (HTML login form reusing the app's existing password-check function; redirect only to the exact, pre-registered, https:// `redirect_uri` — reject non-https/non-localhost schemes at both register time AND authorize time, or DCR becomes an open-redirect vector), `POST /oauth/token` (exchange code + PKCE `code_verifier` for an opaque access token, one-time code consumption via an atomic `UPDATE ... WHERE usado=0`). The MCP endpoint then reads `Authorization: Bearer <access_token>` and returns `401` with `WWW-Authenticate: Bearer resource_metadata="<issuer>/.well-known/oauth-protected-resource"` on failure — that header is what lets Claude auto-discover and start the OAuth flow.

**Why:** matches the one auth mechanism Claude's remote-connector client reliably drives end-to-end (DCR discovery → browser login → PKCE token exchange → Bearer on every call); anything else (URL token, unadvertised headers) connects but leaves tool discovery silently empty.

**How to apply / gotchas:**
- Extract core business logic into a plain function callable by both REST and MCP `tools/call` — never duplicate validation logic.
- Tool identity/owner must come from the authenticated context, never from LLM-supplied arguments.
- If your process has a lazy `init_db()` that historically only ran when the main web page loaded (e.g. inside a Streamlit script), OAuth/MCP routes hit directly by an external client will 500 with "no such table" — call `init_db()` eagerly at module import time in the server entrypoint instead.
- Only restrict who can *log in* via `perfil`/role checks in the `/oauth/authorize` handler (e.g. reject non-target-role accounts) — DCR registration itself is intentionally open per RFC 7591.
- GET on the MCP endpoint can safely return 405 if the server doesn't push server-initiated messages (no SSE).
