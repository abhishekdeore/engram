# Engram + Gemini CLI — Setup Guide

Connect your Engram memory to Google's Gemini CLI so conversations can be saved and retrieved across all your LLMs.

## Prerequisites

- **Gemini CLI** installed ([github.com/google-gemini/gemini-cli](https://github.com/google-gemini/gemini-cli))
- **uv** package manager installed (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- **Engram repo** cloned locally
- **Engram API key** (see Step 1)

## Step 1: Get Your API Key

You need an Engram API key to authenticate. Contact Abhishek Deore:
- Email: abhisdeore4263@gmail.com
- LinkedIn: https://www.linkedin.com/in/abhishekdeore/

You'll receive:
- An **API URL** (e.g., `https://engram-production-d6d1.up.railway.app`)
- An **API key** (a JWT valid for 1 year)

## Step 2: Configure Gemini CLI

Edit your Gemini CLI settings file:

```bash
# Open (or create) the settings file
nano ~/.gemini/settings.json
```

Add the `mcpServers` block. If the file already has content, merge the `mcpServers` key:

```json
{
  "mcpServers": {
    "engram-memory": {
      "command": "uv",
      "args": ["run", "--directory", "/ABSOLUTE/PATH/TO/cross-llm-memory", "engram-mcp"],
      "env": {
        "ENGRAM_API_URL": "https://engram-production-d6d1.up.railway.app",
        "ENGRAM_API_KEY": "YOUR_API_KEY_HERE"
      }
    }
  }
}
```

Replace:
- `/ABSOLUTE/PATH/TO/cross-llm-memory` with the actual path to your cloned Engram repo
- `YOUR_API_KEY_HERE` with your API key from Step 1

## Step 3: Restart Gemini CLI

Close and reopen Gemini CLI. It will automatically detect the new MCP server and load the `memory_write` and `memory_query` tools.

## Step 4: Test It

### Save a conversation

```
You: Tell me about quantum computing
Gemini: [responds with quantum computing information]
You: Save this to my memory
Gemini: [calls memory_write with provider="gemini"] Done. This conversation has been saved to your Engram memory.
```

### Retrieve from another LLM

Open Claude Desktop (or any other connected LLM) and ask:

```
You: Do you remember the quantum computing discussion?
Claude: [calls memory_query] Here is what I found in your memory from GEMINI on 2026-04-04: [verbatim content]
```

## How It Works

```
Gemini CLI → MCP stdio → engram-mcp → HTTPS → Engram API (Railway) → Neo4j
```

- Gemini CLI spawns `engram-mcp` as a local subprocess
- `engram-mcp` is a lightweight HTTP client that forwards all requests to the deployed Engram API
- No database credentials are needed on your machine — only the API URL and key
- Conversations are stored with `provider="gemini"` so you can filter by provider when querying

## Troubleshooting

### "MCP server failed to start"
- Verify the path in `settings.json` is absolute and correct
- Run `uv run --directory /path/to/repo engram-mcp` manually to see errors
- Check that `ENGRAM_API_URL` and `ENGRAM_API_KEY` are set

### "No memories found"
- Memories are only saved when you explicitly ask. Say "save this to my memory"
- Check that the write succeeded (Gemini will confirm)
- Embeddings are computed asynchronously — wait a few seconds before querying

### "Connection refused"
- Verify the API is running: `curl https://engram-production-d6d1.up.railway.app/health`
- Check your API key hasn't expired
