# Engram

> Persistent, verbatim memory across every LLM you use.

Ask Claude what you discussed with ChatGPT last Tuesday. Ask Gemini to continue the research you started with Grok. Engram makes it possible — without modifying, summarising, or hallucinating a single word of your conversations.

---

## How it works

Every conversation you choose to save is stored **word for word** in a Neo4j graph. When you need it back, semantic vector search retrieves the exact text and hands it to whichever model you're talking to next.

The memory pipeline **never calls a generative model**. What goes in comes out unchanged.

---

## Key properties

- **Zero hallucination** — verbatim storage and retrieval, no LLM in the loop
- **Cross-provider** — ChatGPT, Claude, Gemini, Grok, Copilot out of the box
- **User-controlled** — nothing is saved unless you explicitly trigger it
- **Idempotent writes** — sending the same conversation twice changes nothing
- **Date-aware retrieval** — _"the quantum computing conversation from March 5th"_ resolves correctly

---

## Stack

| Layer | Technology |
|-------|-----------|
| API | FastAPI + Uvicorn |
| Graph DB | Neo4j 2026.x |
| Auth | JWT (HS256) |
| Embeddings | OpenAI `text-embedding-3-small` |
| Semantic search | Neo4j vector index, cosine similarity |
| Rate limiting | slowapi (per-user, JWT-decoded key) |
| Cache | Redis (optional — embedding cache) |
| MCP | `mcp==1.26.0`, stdio transport (Claude Desktop) |
| Runtime | Python 3.11, uv |

---

## Quickstart

**1. Prerequisites**
- Neo4j Desktop running locally (bolt://127.0.0.1:7687)
- Python 3.11+
- [uv](https://github.com/astral-sh/uv)

**2. Configure**
```bash
cp .env.example .env
# Fill in NEO4J_PASSWORD and JWT_SECRET_KEY
```

**3. Set up the schema**
```bash
uv run python scripts/setup_schema.py
```

**4. Start the server**
```bash
uv run uvicorn memory.api.main:app --reload --host 0.0.0.0 --port 8000
```

**5. Issue a dev token and write a conversation**
```bash
# Get a token
curl -X POST http://localhost:8000/auth/token \
  -H "Content-Type: application/json" \
  -d '{"userId": "your-user-id"}'

# Store a conversation
curl -X POST http://localhost:8000/memory/write \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d @payload.json
```

API docs available at `http://localhost:8000/docs`.

---

## Data model

```
User
 └─HAS_CONVERSATION──► Conversation
                         └─HAS_MESSAGE──► Message ──NEXT_MESSAGE──► Message
                         └─HAS_SEGMENT──► Segment ──NEXT_SEGMENT──► Segment
                                           └─CONTAINS_MESSAGE──► Message
```

Conversations are segmented into 20-message blocks (`Segment` nodes) — the unit of vector search. The full `Message` chain is always preserved for verbatim retrieval.

---

## Roadmap

- [x] Phase 0 — Neo4j schema, constraints, indexes
- [x] Phase 1 — Write API, segmentation, JWT auth
- [x] Phase 2 — Embedding pipeline, semantic search, query API
- [x] Phase 3 — Read/delete API, provider adapters (ChatGPT · Claude · Gemini · Grok · Copilot), per-user rate limiting, correlation ID observability
- [x] Phase 4 — MCP server (Claude Desktop integration), write retry with exponential backoff

---

## Running tests

```bash
uv run pytest tests/ -v
```

Tests run against a real Neo4j instance. No mocks.

---

## License

MIT
