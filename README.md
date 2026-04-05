<p align="center">
  <img src="assets/banner.svg" alt="Engram - Persistent Verbatim Memory Across Every LLM" width="100%"/>
</p>

<p align="center">
  <a href="https://github.com/abhishekdeore/engram"><img src="https://img.shields.io/badge/status-active%20development-6C63FF?style=flat-square" alt="Status"/></a>
  <a href="https://github.com/abhishekdeore/engram"><img src="https://img.shields.io/badge/python-3.11+-3776ab?style=flat-square&logo=python&logoColor=white" alt="Python"/></a>
  <a href="https://github.com/abhishekdeore/engram"><img src="https://img.shields.io/badge/MCP-Model%20Context%20Protocol-FF6B35?style=flat-square" alt="MCP"/></a>
  <a href="https://github.com/abhishekdeore/engram/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-green?style=flat-square" alt="License"/></a>
</p>

<p align="center">
  <strong>Ask Claude what you discussed with ChatGPT last Tuesday.</strong><br/>
  <strong>Ask Gemini to continue the research you started with Grok.</strong><br/>
  <em>Engram makes it possible -- without modifying, summarising, or hallucinating a single word.</em>
</p>

---

## The Problem

Every LLM has isolated memory. If you research a topic with ChatGPT on Monday, ask Claude about it on Wednesday, and switch to Gemini on Friday -- each one starts from zero. There is no shared memory layer. Your context is trapped inside whichever chat window you happened to use.

This means you repeat yourself, lose continuity, and can never build a persistent knowledge base across the tools you actually use.

## What Engram Does

Engram is a **persistent, verbatim memory layer** that works across every major LLM. When you choose to save a conversation, it is stored **word for word**. When you need it back -- from any LLM, on any device, at any time -- semantic search retrieves the exact text and hands it to whichever model you are talking to.

```
User --> ChatGPT:  "Research quantum computing error correction for me"
ChatGPT responds with a 3000-word document

User --> ChatGPT:  "Save this to my memory"

            --- three days later, different device ---

User --> Claude:   "Do you remember that quantum computing research?"
Claude retrieves the full verbatim ChatGPT exchange from Engram
Claude --> User:   "Yes, here is exactly what was documented: ..."
```

The conversation comes back **word for word**. Not a summary. Not a paraphrase. Not an AI-generated approximation. The exact text.

---

## Why Verbatim Matters

Most memory systems summarise, compress, or distill your conversations before storing them. That means an LLM decides what is important and what gets thrown away -- before you ever ask for it back.

Engram takes a fundamentally different approach:

> **There is no LLM anywhere in the memory pipeline.**

What goes in comes out unchanged. The entire storage and retrieval path is deterministic -- no generative model touches your data at any point. The LLM you are talking to receives the raw, unmodified text. It can then reason about it, summarise it, or build on it -- but the **source material is always the original words**.

This is the difference between a memory system and a hallucination system.

---

## Supported Providers

Engram integrates natively with every major LLM through the **Model Context Protocol (MCP)** -- the emerging standard for how AI models connect to external tools.

| Provider | Status |
|----------|--------|
| **Claude** (Anthropic) | Live |
| **ChatGPT** (OpenAI) | Live (Custom GPT) |
| **Gemini** (Google) | Live (Gemini CLI) |
| **Grok** (xAI) | Planned |
| **Copilot** (Microsoft) | Planned |

All providers read from and write to the **same memory store**. A conversation saved from ChatGPT is immediately available to Claude, Gemini, and every other connected LLM.

---

## How It Works

```
     +---------------------+              +------------------------+
     |   Claude Desktop    |              |                        |
     |   ChatGPT           +--- HTTPS --->+    Engram Cloud        |
     |   Gemini / Grok     |              |    Service             |
     |   Any MCP client    |              |                        |
     +---------------------+              +------------------------+

         No credentials on user's machine -- only API URL + API key
         No generative model in the storage or retrieval pipeline
```

1. You save a conversation from any connected LLM
2. Engram stores it verbatim with full context and metadata
3. When you (or a different LLM) query memory, semantic search finds the most relevant conversations
4. The exact original text is returned -- ready for any model to reason about

Conversations are stored as a knowledge graph, not flat text. This means you can query across providers, filter by date or topic, and retrieve precisely the context you need.

---

## Use Cases

### Cross-LLM Continuity
Start a research thread with ChatGPT, continue it with Claude, refine it with Gemini. Every model sees the full history -- verbatim -- without you copying and pasting between chat windows.

### Personal Knowledge Base
Every important conversation you have with any AI becomes part of a persistent, searchable knowledge base. Job applications, research threads, technical decisions, personal notes -- all stored exactly as discussed, retrievable by any model.

### Agentic Memory
Give your AI agents long-term memory that persists across sessions and providers. An agent can store research findings in one session, retrieve them in the next, and build on work from other agents -- all without re-prompting.

### Team Knowledge (Future)
Multiple users sharing a memory namespace. One team member's ChatGPT research is available to another team member's Claude session -- with ownership and access control.

---

## What Makes Engram Different

| | Engram | Typical AI Memory |
|---|---|---|
| **Storage** | Verbatim -- exact words stored | Summarised or compressed |
| **Retrieval** | Original text returned | AI-generated summary returned |
| **LLM in pipeline** | None -- fully deterministic | LLM decides what to keep |
| **Cross-provider** | Any LLM reads any LLM's conversations | Locked to one provider |
| **Data model** | Knowledge graph with relationships | Flat vector store |
| **User control** | Explicit save trigger only | Often automatic / opaque |

---

## Getting Access

Engram is in active development. Self-registration is not available yet. To get onboarded:

1. **Contact Abhishek Deore** to request an API key and user ID:
   - Email: **abhisdeore4263@gmail.com**
   - LinkedIn: [Abhishek Deore](https://www.linkedin.com/in/abhishekdeore/) -- send a message

2. You will receive:
   - An **API URL** (the Engram server address)
   - An **API Key** (your personal access token, valid for 1 year)

That is all you need. No database credentials, no extra API keys, no server setup.

---

## Setup -- Claude Desktop

**Prerequisites:** macOS or Windows, [Claude Desktop](https://claude.ai/download), Python 3.11+, [uv](https://docs.astral.sh/uv/)

1. Clone the repo and install dependencies:
   ```bash
   git clone https://github.com/abhishekdeore/engram.git
   cd engram
   uv sync
   ```

2. Edit `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):
   ```json
   {
     "mcpServers": {
       "engram-memory": {
         "command": "uv",
         "args": ["run", "engram-mcp"],
         "cwd": "/your/path/to/engram",
         "env": {
           "ENGRAM_API_URL": "<your API URL>",
           "ENGRAM_API_KEY": "<your API key>"
         }
       }
     }
   }
   ```

3. Restart Claude Desktop. Say **"Do you remember..."** or **"Save this to memory"** to use it.

---

## Setup -- ChatGPT (Custom GPT)

1. Go to [chatgpt.com/gpts/editor](https://chatgpt.com/gpts/editor) and create a new GPT
2. Paste the system prompt from [`chatgpt_integration/system_prompt.md`](chatgpt_integration/system_prompt.md)
3. Under **Actions**, click **Import from URL** and paste your Engram action spec URL (provided with your API key)
4. Set authentication: **API Key** / **Bearer** / paste your API key
5. Save the GPT. Done -- say **"Save this conversation"** or **"Do you remember..."**

For detailed instructions, troubleshooting, and the system prompt reference, see [`CHATGPT_SETUP.md`](CHATGPT_SETUP.md).

---

## Setup -- Gemini CLI

1. Install [Gemini CLI](https://github.com/google-gemini/gemini-cli) and [uv](https://astral.sh/uv)
2. Clone this repo and get an API key (contact details above)
3. Add to `~/.gemini/settings.json`:
```json
{
  "mcpServers": {
    "engram-memory": {
      "command": "/path/to/engram/.venv/bin/engram-mcp",
      "args": [],
      "env": {
        "ENGRAM_API_URL": "https://your-engram-server.up.railway.app",
        "ENGRAM_API_KEY": "your-api-key"
      }
    }
  }
}
```
4. Restart Gemini CLI. Done -- say **"Save this conversation"** or **"Do you remember..."**

For detailed instructions and troubleshooting, see [`GEMINI_SETUP.md`](GEMINI_SETUP.md).

---

## Roadmap

- [x] Core memory engine -- verbatim storage, semantic search, graph-based retrieval
- [x] Claude Desktop integration (MCP)
- [x] ChatGPT integration (Custom GPT Action)
- [x] Cloud deployment with authentication and rate limiting
- [x] Gemini integration (Gemini CLI via MCP)
- [ ] Grok and Copilot integrations
- [ ] Production hardening and monitoring
- [ ] Semantic fact versioning across conversations

---

## License

MIT

---

<p align="center">
  <sub>Built by <a href="https://github.com/abhishekdeore">Abhishek Deore</a></sub>
</p>
