---
name: Streamlit auth: bcrypt + idempotent seed
description: Password hashing edge cases and safe first-run seeding pattern for a users table in a Streamlit app.
---

When adding password-hash verification (bcrypt or similar), guard against `None`/empty/non-string hash values *before* calling `.encode()` — catching `ValueError`/`TypeError` alone is not enough; missing/malformed data can also raise `AttributeError`. Always treat any invalid hash as an auth failure, never let it propagate.

**Why:** a code review caught that a malformed or absent `senha_hash` could crash the login path instead of failing closed.

**How to apply:** in the guard clause, check `not senha or not hash_armazenado or not isinstance(hash_armazenado, str)` and return False before touching `.encode()`.

For first-run seeding of an accounts table (e.g. one admin + migrated test users), make the seed function idempotent by checking `count(*) == 0` before inserting — this preserves passwords/status changes made later even if the seed function runs again on every app restart.

Also add a redundant fail-closed profile check at the top of each protected screen/route (not just in the router), so a tampered or inconsistent session object can't render the wrong screen even if the router logic is ever bypassed or changes.
