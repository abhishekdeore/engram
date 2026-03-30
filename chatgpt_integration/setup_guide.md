# ChatGPT Custom GPT — Setup Guide

This guide connects a ChatGPT Custom GPT to your Engram memory instance.

---

## Prerequisites

- Engram server running and reachable from the internet (see note on public URL below)
- ChatGPT Plus or Team subscription (required for Custom GPTs with Actions)
- Your Engram `userId`

---

## Step 1 — Make your server reachable

ChatGPT's servers must be able to reach your Engram API over HTTPS.

**For local testing (quickest):**
```bash
# Install ngrok: https://ngrok.com
ngrok http 8000
# Copy the https://xxxxx.ngrok.io URL
```

**For production:**
Deploy the FastAPI server to Railway, Render, or any host. The URL must be HTTPS.

---

## Step 2 — Generate your API key

With the server running:

```bash
curl -X POST http://localhost:8000/auth/apikey \
  -H "Content-Type: application/json" \
  -d '{"userId": "your-user-id"}'
```

Copy the `api_key` value from the response. This is your Bearer token — store it securely.

---

## Step 3 — Create the Custom GPT

1. Go to [chat.openai.com](https://chat.openai.com) → **Explore GPTs** → **Create**
2. In the **Configure** tab:
   - **Name**: Engram Memory (or any name you prefer)
   - **Instructions**: paste the contents of `system_prompt.md`, replacing `YOUR_USER_ID` with your actual userId

---

## Step 4 — Add the Action

1. Scroll down in the Configure tab and click **Create new action**
2. In the **Schema** field, paste the contents of `action_spec.json`
3. Replace `YOUR_SERVER_URL_HERE` in the schema with your actual server URL (e.g. `https://xxxxx.ngrok.io` or your production domain)
4. Under **Authentication**, select **API Key** → **Bearer**
5. Paste your API key from Step 2
6. Click **Save**

Alternatively: the live server auto-serves a correctly configured spec at:
```
GET /chatgpt/action-spec
```
You can import directly from that URL in the Action editor.

---

## Step 5 — Test it

In the Custom GPT chat:

```
You: Tell me about the history of the Roman Empire.
[GPT responds with information]

You: Save this to my memory.
[GPT calls memory_write → "Done. This conversation has been saved to your Engram memory."]

[Start a new conversation]

You: Do you remember the Roman Empire discussion?
[GPT calls memory_query → returns verbatim content from previous conversation]
```

---

## How it works under the hood

| Action | What happens |
|--------|-------------|
| User says "save this" | GPT calls `POST /chatgpt/write` with the conversation turns |
| Server receives write | Normalises via chatgpt adapter → stores verbatim in Neo4j → computes embeddings async |
| User asks about past topic | GPT calls `POST /memory/query` with a natural language query |
| Server receives query | Embeds query → searches 3 vector indexes in parallel → returns verbatim matches |

Writes are **idempotent** — sending the same `conversationId` twice stores the conversation once.

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| 401 Unauthorized | Check the API key is set correctly under Authentication in the Action config |
| 403 Forbidden | The `userId` in your system prompt must match the userId used to generate the API key |
| 422 No storable messages | The conversation only has system/tool messages — needs at least one user or assistant turn |
| Connection refused | Server is not reachable from the internet — check your ngrok tunnel or deployment URL |
| Empty query results | Embeddings may not be ready yet (they process async) — try again in a few seconds, or the query doesn't semantically match stored content |
