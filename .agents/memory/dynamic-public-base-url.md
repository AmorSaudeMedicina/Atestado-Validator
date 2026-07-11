---
name: Dynamic public base URL from request host
description: How to safely derive a public base URL (for QR codes, verification links, OAuth issuer/metadata) from the incoming request instead of a hardcoded dev domain, in a mixed Streamlit+Starlette app.
---

When an app must generate absolute public URLs (QR codes, verification links, OAuth `issuer`/`authorization_endpoint`/`resource_metadata`) that work on both the Replit dev domain and the published production domain, do not hardcode `REPLIT_DEV_DOMAIN`. Derive the base URL from the actual request instead:

- In Starlette/FastAPI route handlers: read `X-Forwarded-Proto` / `X-Forwarded-Host` (falling back to `Host`), since Replit's proxy always sets these correctly for both dev and prod.
- Inside a Streamlit script (no direct `Request` object available): use `st.context.url` (Streamlit ≥1.59) to get the browser's current URL including scheme+host.
- Fall back to `REPLIT_DEV_DOMAIN` env var / localhost only when neither a request nor a Streamlit context is available (e.g. background scripts).

**Why:** Trusting `Host`/`X-Forwarded-Host` unconditionally is a host-header-injection risk — a forged header can redirect generated links (phishing) or poison OAuth metadata (`issuer`, `authorization_endpoint`) to an attacker-controlled domain, even though the app sits behind Replit's own proxy.

**How to apply:** Validate the extracted host against an allowlist of trusted suffixes (`.replit.dev`, `.replit.app`, `localhost`, `127.0.0.1`, plus any explicitly configured custom domain) and a strict character pattern (no commas/spaces/slashes — reject proxy-chained or injected values) before using it. If validation fails, fall back to the env-var-based dev domain rather than reflecting the untrusted value. Also add a `/healthz` route and set `deploymentTarget = "vm"` (not `autoscale`) when the app persists state to local SQLite or Streamlit in-process session — autoscale's multiple/ephemeral instances break both.
