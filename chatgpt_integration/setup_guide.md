# Engram + ChatGPT — Setup Guide

---

## IMPORTANT: Two Integration Paths

| Path | Status | User Experience |
|------|--------|-----------------|
| **Custom GPT Action (REST)** | ⚠️ Temporary testing only | User must open a specific Custom GPT — does NOT work in normal ChatGPT chats |
| **MCP HTTP/SSE server** | Production target | User adds Engram from normal ChatGPT chats via the App Directory — no Custom GPT needed |

**Why does this distinction matter?**

The Custom GPT approach exists because it can be built and tested today in a few minutes. It is useful for verifying the Engram write/query pipeline works end-to-end before committing to the MCP server build.

However, it requires users to navigate to a specific Custom GPT — it does not appear in normal `@` mentions in standard ChatGPT conversations. It also requires each user to configure their own copy, which does not scale.

The MCP path (HTTP/SSE transport, submitted to the ChatGPT App Directory) is how Engram appears in normal ChatGPT conversations, like any first-party tool. This is the correct long-term architecture. Do not extend the REST bridge pattern to Gemini, Grok, or Copilot — build MCP servers for each.

---

## Part A — Production Path: MCP HTTP/SSE Server

> Status: **BUILT** (Phase 5A permanent complete).
> The server is ready to deploy. The Custom GPT Action in Part B remains available as a testing path.

### What was built

`src/memory/mcp_server_http.py` — a standalone MCP server using the Streamable HTTP transport
(`StreamableHTTPSessionManager`, `stateless=True`). Runs on port 8001 by default.

Key properties:
- Same `memory_write` and `memory_query` tools as the Claude Desktop stdio server — tool logic is shared via `src/memory/mcp_tools.py`, zero duplication
- Bearer token auth: `BearerAuthMiddleware` validates the JWT on every `/mcp` request before the MCP session starts
- Per-request `user_id` isolation via `contextvars.ContextVar` — concurrent requests from different users are safe
- `/health` endpoint (no auth required) for liveness probes
- Configurable via `MCP_HTTP_PORT` (default `8001`) and `MCP_HTTP_HOST` (default `0.0.0.0`) env vars

### Run the MCP HTTP server

```bash
# Start the HTTP/SSE MCP server (separate from the FastAPI server)
uv run engram-mcp-http

# Or with a custom port
MCP_HTTP_PORT=9000 uv run engram-mcp-http
```

### Deployment steps (ops — not yet done)

**1. Deploy publicly**

The MCP HTTP server must be reachable by OpenAI's servers over HTTPS. Deploy as a separate
process on port 8001, or alongside the FastAPI server on a different port. Railway, Render,
Fly.io, and AWS all work.

**2. Register with the ChatGPT App Directory**

- Go to `chatgpt.com/apps` → **Submit an app**
- Provide: server URL (`https://your-domain.com:8001/mcp`), MCP manifest, tool descriptions
- OpenAI reviews and approves
- Once live: users find Engram in the App Directory and add it to their account — then `@Engram` works in any standard ChatGPT conversation

**3. Authentication**

The server currently accepts any valid Engram JWT (from `POST /auth/apikey`) as a Bearer token.
For App Directory submission, OpenAI may require OAuth 2.0 — the `mcp` SDK supports this via
`auth=AuthSettings(...)` in `FastMCP` configuration. The current Bearer-token approach is
sufficient for private/beta deployments.

---

## Part B — Temporary Testing Path: Custom GPT Action

> This is a REST bridge for testing only. Use it to verify the Engram pipeline works with ChatGPT before the MCP server is built. Do NOT extend this pattern to Gemini, Grok, or Copilot.

### Prerequisites

- Engram server running and reachable from the internet
- ChatGPT Plus or Team subscription (required for Custom GPTs with Actions)
- Your Engram `userId`

---

### Step 1 — Make your server reachable

ChatGPT's servers must reach your Engram API over HTTPS.

**For local testing:**
```bash
# Install ngrok: https://ngrok.com
ngrok http 8000
# Copy the https://xxxxx.ngrok-free.app URL
```

**For persistent testing:**
Deploy the FastAPI server to Railway, Render, or any host with a stable HTTPS URL.

---

### Step 2 — Generate your API key

With the server running locally:

```bash
curl -X POST http://localhost:8000/auth/apikey \
  -H "Content-Type: application/json" \
  -d '{"userId": "your-user-id"}'
```

Response:
```json
{
  "api_key": "eyJ...",
  "token_type": "bearer",
  "userId": "your-user-id",
  "expires_at": "2027-03-29T12:00:00+00:00"
}
```

Copy the `api_key` value. This is your Bearer token — store it securely. It is valid for 1 year.

Note: `userId` must be consistent — the same value used to generate the key must be placed in the system prompt (Step 4). Any mismatch returns 403.

---

### Step 3 — Create the Custom GPT

1. Go to [chat.openai.com](https://chat.openai.com) → **Explore GPTs** → **Create**
2. Open the **Configure** tab
3. Set **Name**: `Engram Memory` (or any name you prefer)
4. Set **Instructions**: paste the full contents of `system_prompt.md`, then replace `YOUR_USER_ID` with your actual userId

---

### Step 4 — Add the Action

Two options — use whichever is easier:

**Option A — Import from live server (recommended):**
1. Click **Create new action**
2. Click **Import from URL**
3. Enter: `https://your-server-url/chatgpt/action-spec`
   The server injects the correct base URL automatically.

**Option B — Paste the spec manually:**
1. Click **Create new action**
2. In the **Schema** field, paste the contents of `action_spec.json`
3. Find `YOUR_SERVER_URL_HERE` in the schema and replace it with your actual server URL

**In both cases:**
- Under **Authentication**, select **API Key** → **Bearer**
- Paste your API key from Step 2
- Click **Save**

---

### Step 5 — Test it

Open the Custom GPT and verify:

```
You: Tell me about the history of the Roman Empire.
[GPT responds]

You: Save this to my memory.
[GPT calls memory_write → "Done. Saved to your Engram memory."]

--- start a new conversation in the same Custom GPT ---

You: Do you remember the Roman Empire discussion?
[GPT calls memory_query → returns verbatim content]
```

Cross-provider test (requires Claude Desktop with MCP already configured):
```
[In Claude Desktop]
User: Do you remember the Roman Empire discussion from ChatGPT?
[Claude calls memory_query → same verbatim content returned]
```

---

### How it works under the hood

| Action | What happens |
|--------|-------------|
| User says "save this" | GPT calls `POST /chatgpt/write` with the conversation turns |
| Server receives write | Normalises via chatgpt adapter → stores verbatim in Neo4j → computes embeddings async (202 response) |
| User asks about past topic | GPT calls `POST /memory/query` with a natural language query |
| Server receives query | Embeds query → searches 3 vector indexes in parallel → returns verbatim matches ranked by relevance |

Writes are **idempotent** — sending the same `conversationId` twice stores the conversation once.

---

### Troubleshooting

| Issue | Fix |
|-------|-----|
| 401 Unauthorized | Check the API key is set correctly under Authentication in the Action config |
| 403 Forbidden | The `userId` in your system prompt must exactly match the userId used to generate the API key |
| 422 No storable messages | The conversation only contains system/tool messages — needs at least one user or assistant turn |
| Connection refused | Server not reachable from the internet — check your ngrok tunnel or deployment URL |
| Empty query results | Embeddings process async (~300ms after write) — wait a moment; or the query may not semantically match stored content |
| ngrok URL changed | ngrok free tier generates a new URL on each restart — update the Action spec with the new URL |

---

## Known Limitations of the Custom GPT Path

1. **Not in normal ChatGPT chats** — users must always open the Custom GPT specifically. `@Engram` does not work in standard chats.
2. **Per-user setup required** — each user must create their own Custom GPT and configure their own API key. Not scalable.
3. **No revocation** — if an API key is compromised, the only recourse is rotating `JWT_SECRET_KEY` (invalidates all keys for all users). See BACKLOG.md.
4. **ngrok URLs expire** — free ngrok tunnels change on restart; testing sessions can break unexpectedly.

These limitations do not exist with the MCP HTTP/SSE server (Part A). The Custom GPT path is retained only for testing the write/query pipeline today.

---

## Part C — Production Submission: ChatGPT App Directory

> This is how Engram appears in **normal ChatGPT chats** for any user — no Custom GPT, no
> per-user setup. Users browse the App Directory, connect Engram once, and then activate it
> in any chat via the `+` (Tools) button or by typing `@Engram`.
>
> **Prerequisites before starting this section:**
> - `mcp_server_http.py` is deployed publicly on a stable HTTPS domain (not ngrok)
> - Your domain is verifiable (you can serve a static file at `/.well-known/openai-apps-challenge`)
> - You have a verified OpenAI organization (individual or business identity verification required)
> - You are using a **global data residency** OpenAI project — EU data residency projects cannot submit apps

---

### What You Are Submitting

An MCP server running the **Streamable HTTP transport** (not the old SSE transport). ChatGPT
connects to your `/mcp` endpoint, discovers the `memory_write` and `memory_query` tools, and
calls them directly inside normal chat sessions.

The `mcp_server_http.py` already implements Streamable HTTP via `StreamableHTTPSessionManager`.
The primary remaining work before submission is:

1. Deploy it publicly with HTTPS
2. Add OAuth 2.1 (ChatGPT does not support static API keys for App Directory apps)
3. Add required tool annotations
4. Complete the submission form

---

### Step 1 — Deploy the MCP HTTP Server Publicly

The server must be reachable by OpenAI over HTTPS. ngrok is acceptable for testing but not
for App Directory submission.

**Recommended deployment (Railway or Render):**

```bash
# The MCP HTTP server is a standalone Uvicorn/Starlette app
# Entry point: engram-mcp-http (port 8001 by default)
# Env vars needed on the host:
#   MCP_HTTP_PORT=443 (or let the platform handle it)
#   MCP_HTTP_HOST=0.0.0.0
#   NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD, NEO4J_DATABASE
#   OPENAI_API_KEY
#   JWT_SECRET_KEY
```

Your public MCP endpoint will be:
```
https://your-domain.com/mcp
```

---

### Step 2 — Add OAuth 2.1 (Required for App Directory)

> **Critical:** ChatGPT does not support static API keys for App Directory apps.
> The Bearer token auth currently in `mcp_server_http.py` works for private/testing use
> but must be replaced with OAuth 2.1 + PKCE for the public App Directory.

**What ChatGPT expects:**

ChatGPT performs the following sequence when a user connects Engram for the first time:

1. Fetches `GET https://your-domain.com/.well-known/oauth-protected-resource`
2. Fetches your OAuth authorization server metadata
3. Performs **Dynamic Client Registration (DCR)** — registers a new `client_id` per user session
4. Launches Authorization Code + PKCE (S256) flow — user approves in a browser popup
5. Exchanges auth code for access token
6. Sends `Authorization: Bearer <token>` on all subsequent `/mcp` requests

**Your OAuth server must expose:**

```
GET /.well-known/oauth-protected-resource
→ { "resource": "https://your-domain.com", "authorization_servers": ["https://your-domain.com"], "scopes_supported": ["memory:read", "memory:write"] }

GET /.well-known/oauth-authorization-server
→ { "authorization_endpoint", "token_endpoint", "registration_endpoint", "code_challenge_methods_supported": ["S256"] }
```

**OAuth redirect URI to allow:**
```
https://platform.openai.com/apps-manage/oauth
```

**Important gotcha — state parameter size:** OpenAI's OAuth state parameter is 400+ characters
of base64-encoded JSON. Store it in a `TEXT` column, not `VARCHAR(255)`.

**Implementation options:**
- Use an auth provider (Auth0, Keycloak, Supabase Auth) — configure DCR and PKCE in their dashboard
- Build a minimal OAuth server in FastAPI alongside the existing REST API (recommended for Engram — keeps everything in one codebase)

---

### Step 3 — Add Required Tool Annotations to `mcp_tools.py`

Every tool must declare three annotations — these are **mandatory** for App Directory review.
Missing any of them is treated as a validation failure.

In `src/memory/mcp_tools.py`, update the tool definitions to include:

```python
# For memory_write:
annotations={
    "readOnlyHint": False,       # write tool — modifies data
    "openWorldHint": False,      # bounded target (user's own Neo4j graph only)
    "destructiveHint": False,    # writes are idempotent, not destructive
}

# For memory_query:
annotations={
    "readOnlyHint": True,        # read-only — no data modification
    "openWorldHint": False,      # bounded target (user's own Neo4j graph only)
    "destructiveHint": False,    # read only, never destructive
}
```

---

### Step 4 — Add Domain Verification Endpoint

OpenAI tests this endpoint immediately when you click Submit. It must be live before submission.

Add to your FastAPI app (`main.py`) or serve as a static file:

```python
@app.get("/.well-known/openai-apps-challenge")
async def openai_challenge():
    # Replace with the token OpenAI provides during submission
    return PlainTextResponse("OPENAI_PROVIDED_TOKEN_HERE")
```

---

### Step 5 — Submit via the OpenAI Platform Dashboard

1. Go to `platform.openai.com` → **ChatGPT Apps** → **+ New App**
2. Fill in the submission form:

**App Identity:**
| Field | Value |
|-------|-------|
| App name | Engram Memory |
| Logo/icon | SVG, 64×64px — test in dark mode |
| Subtitle | Persistent verbatim memory across every LLM |
| Description | Engram stores and retrieves your LLM conversations word-for-word across ChatGPT, Claude, Gemini, and more. Nothing is summarised or modified. |
| Category | Productivity |
| Privacy policy URL | your-domain.com/privacy |
| Terms of service URL | your-domain.com/terms |

**MCP Server:**
| Field | Value |
|-------|-------|
| MCP server URL | `https://your-domain.com/mcp` |
| OAuth redirect URI | `https://platform.openai.com/apps-manage/oauth` |

3. Click **Scan Tools** — OpenAI calls your server's `tools/list`. Both `memory_write` and `memory_query` must appear with their annotations.

4. **Tool justifications** (required for each tool):

For `memory_write`:
> Read-only: No — this tool writes conversation data to a Neo4j graph database owned by the authenticated user. Open-world: No — the tool only writes to the user's own isolated memory graph; it cannot access arbitrary external systems. Destructive: No — all writes use MERGE semantics (idempotent); re-sending the same conversation is a no-op.

For `memory_query`:
> Read-only: Yes — this tool only retrieves data; it does not modify anything. Open-world: No — retrieval is scoped to the authenticated user's own memory graph via userId predicate enforced at the Cypher level. Destructive: No — read-only operation with no side effects.

5. **Test cases** (minimum 5 positive, 3 negative):

Positive:
- "Save this conversation to my memory" → `memory_write` → "Conversation queued for storage"
- "Remember what we just discussed" → `memory_write` → "Conversation queued for storage"
- "Do you remember the quantum computing research?" → `memory_query` → verbatim content returned
- "What did I say about my favorite color?" → `memory_query` → verbatim content returned
- "Pull up our conversation about machine learning from last week" → `memory_query` → verbatim content returned

Negative:
- "What's the weather today?" → no tool called (not a memory operation)
- "Write me a poem about the ocean" → no tool called (generative request)
- "What did you just say?" (referring to current session) → no tool called (refers to current context, not stored memory)

6. **Screenshots:** Exactly 706px wide, 400–860px tall. Capture:
   - A ChatGPT chat showing `@Engram` being triggered
   - The tool call debug panel showing `memory_query` returning verbatim results
   - Cross-LLM example: memory written from Claude visible in ChatGPT

7. Click **Submit for Review** — you receive a Case ID by email.

---

### Step 6 — After Approval

1. OpenAI emails you when the review is complete.
2. Log back into `platform.openai.com` → ChatGPT Apps → your app → click **Publish**.
3. The app is now live in the App Directory at `chatgpt.com/apps`.
4. Users connect it once via **Settings → Apps** or find it in the directory.
5. In any normal chat: click `+` (Tools) → More → Engram, or type `@Engram`.
6. `memory_write` and `memory_query` are now available in all standard ChatGPT conversations — no Custom GPT needed.

---

### Review Process and Timeline

| Stage | Notes |
|-------|-------|
| Submission | Automated scan runs immediately (MCP connectivity, tool annotations, domain verification) |
| Review | No published SLA — OpenAI states timelines vary. Expect days to weeks. |
| Outcome | Approved → you manually publish. Rejected → feedback provided; must start a new submission (no amend flow — save your form content locally before submitting). |
| Post-publication | Apps that are unstable or non-compliant may be removed. Monitor uptime on your MCP server. |

---

### Known Limitations and Gotchas

| Issue | Detail |
|-------|--------|
| No API key support | ChatGPT cannot pass static API keys to App Directory apps. OAuth 2.1 is mandatory. |
| DCR at scale | Each user session registers a new OAuth client dynamically. Design your auth server for thousands of short-lived clients. |
| EU data residency | Projects with EU data residency cannot submit apps. Use a global project. |
| Rejection = start over | There is no "amend and resubmit" flow. Copy your form content before clicking Submit. |
| Streamable HTTP only | App Directory uses Streamable HTTP (`/mcp` endpoint, POST + GET). The old SSE transport (`/sse` endpoint) is deprecated — do not use it. Some older OpenAI docs still show SSE; those pages refer to the Responses API (API-to-API), not ChatGPT Apps. |
| Tool annotations mandatory | `readOnlyHint`, `openWorldHint`, `destructiveHint` must be present on every tool. Missing = validation failure. |
| Screenshot dimensions exact | Must be exactly 706px wide, 400–860px tall. |
| State parameter size | OAuth state from OpenAI is 400+ characters. Store in TEXT column, not VARCHAR(255). |
| Business/Enterprise workspaces | Workspace admins must enable apps before users can connect them. |
