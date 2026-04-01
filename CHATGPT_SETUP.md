# Engram + ChatGPT Setup

Set up Engram as a Custom GPT so ChatGPT can read and write to your cross-LLM memory.

---

## Prerequisites

You need an **API URL** and **API Key** from the Engram maintainer. Self-registration is not yet available.

**Contact Abhishek Deore to get onboarded:**
- Email: **abhisdeore4263@gmail.com**
- LinkedIn: [Abhishek Deore](https://www.linkedin.com/in/abhishekdeore/)

You will receive:
- An **API URL** (the Engram server address)
- An **API Key** (a JWT that encodes your userId, valid for 1 year)

---

## Step 1 -- Create the Custom GPT

1. Go to [chatgpt.com/gpts/editor](https://chatgpt.com/gpts/editor) and create a new GPT
2. Set **Name** to `Engram Memory` (or any name you prefer)
3. In the **Instructions** field, paste the full contents of [`chatgpt_integration/system_prompt.md`](chatgpt_integration/system_prompt.md)
4. Replace `YOUR_USER_ID` in the pasted prompt with the userId you received during onboarding

---

## Step 2 -- Add the Action

1. Under **Actions**, click **Create new action**
2. Click **Import from URL** and enter:
   ```
   https://engram-production-d6d1.up.railway.app/chatgpt/action-spec
   ```
   The server returns the correct OpenAPI spec automatically.
3. Under **Authentication**, select **API Key** / **Bearer**
4. Paste your API key
5. Click **Save**

---

## Step 3 -- Test It

Open the Custom GPT and try:

```
You: Tell me about the history of the Roman Empire.
[GPT responds]

You: Save this to my memory.
[GPT calls memory_write -> "Done. Saved to your Engram memory."]
```

Start a new conversation in the same Custom GPT:

```
You: Do you remember the Roman Empire discussion?
[GPT calls memory_query -> returns the exact verbatim content]
```

**Cross-provider test** (requires Claude Desktop with Engram MCP configured):

```
[In Claude Desktop]
You: Do you remember the Roman Empire discussion from ChatGPT?
[Claude retrieves the same verbatim content from Engram]
```

---

## System Prompt Reference

The system prompt instructs ChatGPT when and how to call Engram's memory tools:

- **`memory_write`** -- called only when you explicitly ask to save ("save this", "remember this", "store this conversation")
- **`memory_query`** -- called when you ask about past conversations ("do you remember...", "what did we discuss about...")

The full system prompt is in [`chatgpt_integration/system_prompt.md`](chatgpt_integration/system_prompt.md). Do not modify it except for replacing `YOUR_USER_ID`.

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| 401 Unauthorized | Check the API key is set correctly under Authentication in the Action config |
| 403 Forbidden | The `userId` in the system prompt must exactly match the userId encoded in your API key |
| 422 No storable messages | The conversation only has system/tool messages -- needs at least one user or assistant turn |
| Empty query results | Embeddings process async (~300ms after write) -- wait a moment and try again |

---

## Also See

- [README.md](README.md) -- full project overview, Claude Desktop setup, architecture
- [chatgpt_integration/system_prompt.md](chatgpt_integration/system_prompt.md) -- the system prompt to paste into your Custom GPT
