---
name: Exposing an MCP connector alongside a REST API with per-user tokens
description: How to add a Claude-compatible remote MCP connector to an app that already has token-based REST auth, without a new auth mechanism.
---

When a user wants "Claude to call this API directly" in addition to an existing REST API, add a minimal hand-rolled MCP server (Streamable HTTP transport: single POST route accepting JSON-RPC 2.0 `initialize`/`tools/list`/`tools/call`/notifications, responding with plain JSON — no SSE/session needed for simple tool calls). No SDK required.

**Auth pattern:** embed the existing per-user API token directly in the connector's URL path (e.g. `/mcp/{token}`), reusing the same hash-lookup used by the REST API. Claude's custom-connector UI has no simple static-bearer-header field (only OAuth or bare URL), so a per-user URL is the practical way to bind a connector to one account without building an OAuth server.

**Why:** keeps a single auth mechanism (one token = one identity) shared by REST + MCP + UI; revoking the token in one place invalidates all three surfaces at once.

**How to apply / gotchas:**
- Extract the core business logic (validation + write) into a plain function callable by both the REST handler and the MCP `tools/call` handler — never let the MCP path duplicate validation logic.
- The tool's identity/owner must come from the authenticated context (resolved from the token), never from arguments the LLM passes — otherwise a caller could "forge" another user via tool arguments.
- Token-in-URL leaks into access logs (uvicorn/proxy). Mitigate by adding a `logging.Filter` on `uvicorn.access` that regex-redacts the token segment before it's written, e.g. `/mcp/<token>` → `/mcp/***`. Cheap and effective; don't skip it.
- GET on the MCP endpoint can safely return 405 if the server doesn't push server-initiated messages (no SSE) — spec-compliant and simpler.
