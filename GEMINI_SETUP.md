# Engram + Gemini CLI Setup

Connect Engram to Google's Gemini CLI so your conversations are saved and retrievable across all your LLMs (Claude, ChatGPT, Gemini, Grok, Copilot).

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

## Quick Setup

1. Install [Gemini CLI](https://github.com/google-gemini/gemini-cli) and [uv](https://astral.sh/uv)
2. Clone this repo: `git clone https://github.com/abhishekdeore/engram.git`
3. Edit `~/.gemini/settings.json` and add the MCP server config (see [setup guide](gemini_integration/setup_guide.md))
4. Restart Gemini CLI
5. Say "save this to my memory" to test

---

## Detailed Guide

See [`gemini_integration/setup_guide.md`](gemini_integration/setup_guide.md) for the full step-by-step walkthrough with troubleshooting.

## System Prompt

See [`gemini_integration/system_prompt.md`](gemini_integration/system_prompt.md) for custom instructions to paste into Gemini CLI or a Gem.

## How It Works

Gemini CLI connects to Engram via MCP (Model Context Protocol) — the same open standard used by Claude Desktop. The `engram-mcp` server runs as a local subprocess and forwards requests to the deployed Engram API over HTTPS. No database credentials are needed on your machine.

```
Gemini CLI  ──MCP stdio──>  engram-mcp  ──HTTPS──>  Engram API  ──>  Neo4j
```

Conversations saved from Gemini are tagged with `provider="gemini"` and retrievable from any connected LLM.
