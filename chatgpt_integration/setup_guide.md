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
