---
name: Embedding a REST API inside a Streamlit app (same process/port)
description: How to add custom HTTP endpoints to a Streamlit app without a separate service/language, using Streamlit's Starlette-based App class.
---

Streamlit 1.59+ serves apps via Starlette/uvicorn internally (`streamlit.web.server.starlette.App`), not Tornado. This means custom REST routes can be added in-process, sharing the same DB/filesystem, instead of standing up a second service in another language.

**Pattern:**
```python
from streamlit.web.server.starlette import App
from starlette.routing import Route

app = App(absolute_path_to_script, routes=[Route("/api/foo", handler, methods=["POST"])])
# run with: uvicorn.run(app, host="0.0.0.0", port=int(os.environ["PORT"]))
```
Replace the workflow's `streamlit run app.py ...` command with `python server.py` (or similar custom entrypoint) via `verifyAndReplaceArtifactToml`.

**Why:** avoids cross-language/cross-runtime access to the same SQLite/data file (e.g. a separate Node API server touching a Python app's DB), which is fragile and risks concurrency bugs. User routes take priority over Streamlit's own routes, and reserved prefixes (`/_stcore/`, `/media/`, etc.) can't be overridden.

**How to apply / gotchas:**
- Pass an **absolute** script path to `App(...)`. A relative path (e.g. `"app.py"`) resolves against the process **cwd**, not the script's own directory — if the workflow's run command executes from a different directory (common when one artifact dir shells out to another, e.g. `python ../other-artifact/server.py`), this throws `FileNotFoundError: Streamlit script not found`. Resolve via `Path(__file__).resolve().parent / "app.py"`.
- `.streamlit/config.toml` (theme, CORS, XSRF) still loads correctly as long as the resolved script path points into the directory containing that config — verified empirically via `streamlit.config.get_option(...)`, not just assumed.
- For a public-facing endpoint that must be fetchable by external tools (e.g. embedding a QR code image URL in Canva), only the resource token itself (e.g. a long random code) needs to be secret — the endpoint can be unauthenticated at the same trust level as an existing public verification page.
