"use client";

import { useState } from "react";
import { motion, AnimatePresence } from "motion/react";
import Nav from "@/components/layout/Nav";

// ─── Sidebar sections ───────────────────────────────────────────
const SIDEBAR = [
  {
    group: "Getting Started",
    items: [
      { id: "overview", label: "Overview" },
      { id: "quickstart", label: "Quickstart" },
      { id: "core-concepts", label: "Core Concepts" },
    ],
  },
  {
    group: "Architecture",
    items: [
      { id: "data-model", label: "Graph Data Model" },
      { id: "write-pipeline", label: "Write Pipeline" },
      { id: "retrieval-pipeline", label: "Retrieval Pipeline" },
      { id: "segmentation", label: "Segmentation" },
    ],
  },
  {
    group: "Integrations",
    items: [
      { id: "mcp-claude", label: "Claude (MCP)" },
      { id: "mcp-chatgpt", label: "ChatGPT (MCP HTTP)" },
      { id: "providers", label: "Provider Adapters" },
    ],
  },
  {
    group: "API Reference",
    items: [
      { id: "api-write", label: "POST /memory/write" },
      { id: "api-query", label: "POST /memory/query" },
      { id: "api-conversations", label: "GET /memory/conversations" },
      { id: "api-delete", label: "DELETE endpoints" },
      { id: "api-auth", label: "Authentication" },
    ],
  },
  {
    group: "Operations",
    items: [
      { id: "performance", label: "Performance" },
      { id: "roadmap", label: "Roadmap" },
    ],
  },
];

// ─── Content sections ───────────────────────────────────────────

function CodeBlock({ title, children }: { title?: string; children: string }) {
  return (
    <div className="rounded-xl border border-[rgba(255,255,255,0.07)] overflow-hidden shadow-[0_2px_12px_rgba(0,0,0,0.3)] my-5">
      {title && (
        <div className="flex items-center gap-2 px-4 py-2.5 bg-[#1C1B22] border-b border-[rgba(255,255,255,0.06)]">
          <div className="flex items-center gap-1.5">
            <div className="w-[9px] h-[9px] rounded-full bg-[#FF5F57]" />
            <div className="w-[9px] h-[9px] rounded-full bg-[#FEBC2E]" />
            <div className="w-[9px] h-[9px] rounded-full bg-[#28C840]" />
          </div>
          <span className="ml-2 text-[10px] font-mono text-[#7E7B93]">{title}</span>
        </div>
      )}
      <pre className="bg-[#13121A] p-4 text-xs sm:text-sm font-mono text-[#8B8A94] overflow-x-auto leading-relaxed">
        <code>{children}</code>
      </pre>
    </div>
  );
}

function H2({ children }: { children: React.ReactNode }) {
  return <h2 className="text-2xl sm:text-3xl font-semibold tracking-[-0.01em] mt-14 mb-4">{children}</h2>;
}

function H3({ children }: { children: React.ReactNode }) {
  return <h3 className="text-lg sm:text-xl font-semibold mt-8 mb-3">{children}</h3>;
}

function P({ children }: { children: React.ReactNode }) {
  return <p className="text-[#8B8A94] text-sm leading-relaxed mb-4">{children}</p>;
}

function Highlight({ children }: { children: React.ReactNode }) {
  return <span className="text-[#E09F3E] font-medium">{children}</span>;
}

function InlineCode({ children }: { children: React.ReactNode }) {
  return (
    <code className="px-1.5 py-0.5 rounded bg-[rgba(255,255,255,0.06)] text-[#E2DFD6] text-xs font-mono">
      {children}
    </code>
  );
}

function Table({ headers, rows }: { headers: string[]; rows: string[][] }) {
  return (
    <div className="overflow-x-auto my-5 rounded-lg border border-[rgba(255,255,255,0.06)]">
      <table className="w-full text-xs sm:text-sm">
        <thead>
          <tr className="bg-[rgba(255,255,255,0.03)]">
            {headers.map((h) => (
              <th key={h} className="text-left px-4 py-2.5 font-medium text-[#E2DFD6] border-b border-[rgba(255,255,255,0.06)]">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} className="border-b border-[rgba(255,255,255,0.03)] last:border-0">
              {row.map((cell, j) => (
                <td key={j} className="px-4 py-2.5 text-[#8B8A94]">
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Callout({ children }: { children: React.ReactNode }) {
  return (
    <div className="my-5 p-4 rounded-lg border-l-2 border-[#E09F3E] bg-[rgba(224,159,62,0.06)] text-sm text-[#C4A882] leading-relaxed">
      {children}
    </div>
  );
}

// ─── Doc content by section id ──────────────────────────────────

const CONTENT: Record<string, React.ReactNode> = {
  overview: (
    <>
      <H2>Overview</H2>
      <P>
        <Highlight>Engram</Highlight> is a cross-LLM, persistent, verbatim conversation memory layer built on Neo4j.
        It lets you save a conversation from any LLM provider and retrieve it word-for-word from any other.
      </P>
      <Callout>
        The memory pipeline <strong>never calls a generative model</strong>. What goes in comes out unchanged.
        Zero hallucination by design.
      </Callout>
      <H3>The Problem</H3>
      <P>
        Every LLM has isolated memory. Research done in ChatGPT on Monday is invisible to Claude on Wednesday.
        If you switch to Gemini on Friday, it starts from zero. There is no shared memory layer.
        Engram is that layer.
      </P>
      <H3>The User Experience</H3>
      <CodeBlock title="cross-llm workflow">{`User → ChatGPT:
  "Research quantum computing error correction"

ChatGPT → User:
  [3000-word detailed response]

User → ChatGPT:
  "Save this to my memory"   ← explicit trigger

--- days later ---

User → Claude:
  "Do you remember the quantum computing research?"

Claude → Engram → Claude:
  [Returns full verbatim ChatGPT exchange, word for word]`}</CodeBlock>
      <H3>What Engram is NOT</H3>
      <P>
        Not a summarization service. Not a knowledge extraction pipeline. Not a fact database.
        Not a RAG system that rewrites content. Engram stores and retrieves. That is all.
      </P>
    </>
  ),

  quickstart: (
    <>
      <H2>Quickstart</H2>
      <H3>Prerequisites</H3>
      <P>Neo4j Desktop running on bolt://127.0.0.1:7687, Python 3.11+, and uv package manager.</P>
      <H3>1. Configure</H3>
      <CodeBlock title="terminal">{`cd cross-llm-memory
cp .env.example .env
# Fill in NEO4J_PASSWORD and JWT_SECRET_KEY`}</CodeBlock>
      <H3>2. Set up the schema</H3>
      <CodeBlock title="terminal">{`uv run python scripts/setup_schema.py`}</CodeBlock>
      <H3>3. Seed sample data (optional)</H3>
      <CodeBlock title="terminal">{`uv run python scripts/seed_sample_data.py`}</CodeBlock>
      <H3>4. Start the server</H3>
      <CodeBlock title="terminal">{`uv run uvicorn memory.api.main:app --reload --host 0.0.0.0 --port 8000`}</CodeBlock>
      <H3>5. Start the MCP HTTP server</H3>
      <CodeBlock title="terminal">{`uv run engram-mcp-http
# Listens on 0.0.0.0:8001 (MCP_HTTP_PORT env var)`}</CodeBlock>
      <H3>6. Smoke test</H3>
      <CodeBlock title="terminal">{`# Get a dev token
TOKEN=$(curl -s -X POST http://localhost:8000/auth/token \\
  -H "Content-Type: application/json" \\
  -d '{"userId": "my-user-id"}' | python3 -c \\
  "import sys,json; print(json.load(sys.stdin)['access_token'])")

# Write a conversation
curl -X POST http://localhost:8000/memory/write \\
  -H "Authorization: Bearer $TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{
    "userId": "my-user-id",
    "conversationId": "test-001",
    "provider": "chatgpt",
    "model": "gpt-4o",
    "messages": [
      {"messageId": "m1", "role": "user",
       "content": "What is quantum entanglement?",
       "timestamp": "2026-03-28T10:00:00Z", "tokenCount": 7},
      {"messageId": "m2", "role": "assistant",
       "content": "Quantum entanglement is a phenomenon...",
       "timestamp": "2026-03-28T10:00:05Z", "tokenCount": 42}
    ]
  }'
# → 202 Accepted

# Query it back
curl -X POST http://localhost:8000/memory/query \\
  -H "Authorization: Bearer $TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{"userId": "my-user-id", "query": "quantum entanglement", "topK": 3}'
# → verbatim content returned`}</CodeBlock>
      <H3>7. Run tests</H3>
      <CodeBlock title="terminal">{`uv run pytest tests/ -v
# Expected: 242 passed, 1 skipped`}</CodeBlock>
    </>
  ),

  "core-concepts": (
    <>
      <H2>Core Concepts</H2>
      <H3>The Four Optimization Targets</H3>
      <P>Every architecture decision is evaluated against these criteria, in this order:</P>
      <Table
        headers={["Priority", "Target", "Meaning"]}
        rows={[
          ["1", "Zero Hallucination", "Nothing stored or returned was invented by a model"],
          ["2", "Accuracy", "What is retrieved is exactly what happened"],
          ["3", "Speed", "Retrieval must be fast enough for real-time use"],
          ["4", "Cost", "Minimize API calls and compute"],
        ]}
      />
      <H3>User-Controlled Storage</H3>
      <Callout>
        Storage is <strong>never automatic</strong>. A conversation is saved only when the user explicitly triggers it
        by telling the LLM to save. No heuristic. No automation. No background process.
      </Callout>
      <P>
        The LLM client detects the user&apos;s save intent naturally (it is a language model) and calls the{" "}
        <InlineCode>memory_write</InlineCode> tool. The memory service only receives and stores what it is sent.
      </P>
      <H3>Verbatim Fidelity</H3>
      <Table
        headers={["Allowed", "Not Allowed"]}
        rows={[
          ["Store full verbatim message content", "Summarize before storing"],
          ["Return exact stored text to LLM", "Rephrase or reformat stored content"],
          ["Embed text for similarity search", "Use generative model for extraction"],
          ["Rank results by cosine similarity", "Generate new text during retrieval"],
        ]}
      />
      <H3>Idempotent Writes</H3>
      <P>
        All writes use <InlineCode>MERGE</InlineCode> on stable IDs. Sending the same conversation twice changes
        nothing. Message counts and token counts are only incremented for genuinely new messages.
      </P>
    </>
  ),

  "data-model": (
    <>
      <H2>Graph Data Model</H2>
      <P>The Neo4j graph has five node types connected by six relationship types.</P>
      <H3>Node Types</H3>
      <Table
        headers={["Node", "Purpose", "Key Properties"]}
        rows={[
          ["User", "Unique user identity across all providers", "userId, createdAt"],
          ["Conversation", "One complete session with one LLM", "conversationId, provider, model, messageCount, totalTokens"],
          ["Segment", "20-message block -- primary vector search unit", "content (verbatim concat), embedding (1536d), tokenCount"],
          ["Message", "Single verbatim turn (user or assistant)", "content, role, embedding, timestamp, tokenCount"],
          ["Chunk", "Sub-slice of large messages (>512 tokens)", "content (slice), embedding, chunkIndex"],
        ]}
      />
      <H3>Relationships</H3>
      <CodeBlock title="graph relationships">{`(:User)-[:HAS_CONVERSATION]->(:Conversation)
(:Conversation)-[:HAS_SEGMENT {index}]->(:Segment)
(:Segment)-[:NEXT_SEGMENT]->(:Segment)
(:Segment)-[:CONTAINS_MESSAGE {order}]->(:Message)
(:Message)-[:NEXT_MESSAGE]->(:Message)
(:Message)-[:HAS_CHUNK {index}]->(:Chunk)`}</CodeBlock>
      <H3>Indexes</H3>
      <Table
        headers={["Index", "Type", "Purpose"]}
        rows={[
          ["index_vector_segment", "Vector (1536d, cosine)", "Semantic search on segments"],
          ["index_vector_message", "Vector (1536d, cosine)", "Semantic search on messages"],
          ["index_vector_chunk", "Vector (1536d, cosine)", "Semantic search on chunks"],
          ["index_fulltext_message", "Full-text (Lucene)", "Keyword fallback when vector < 0.70"],
          ["Composite (userId, startedAt)", "B-tree", "Date-filtered user queries"],
        ]}
      />
      <Callout>
        Chunks are never returned to the LLM client. When a chunk matches a query, the system fetches
        and returns the full parent Message node. Chunks are only index artifacts.
      </Callout>
    </>
  ),

  "write-pipeline": (
    <>
      <H2>Write Pipeline</H2>
      <P>The write path is designed for low latency and zero data loss.</P>
      <Table
        headers={["Step", "Action", "Timing"]}
        rows={[
          ["1", "Validate JWT, check required fields", "<5ms (sync)"],
          ["2", "Return 202 Accepted", "Immediate"],
          ["3", "Write verbatim to Neo4j (MERGE)", "<20ms (async)"],
          ["4", "Segmentation check (every 20 messages)", "<10ms"],
          ["5", "Compute embeddings (OpenAI text-embedding-3-small)", "~250-300ms"],
          ["6", "Store vectors on Neo4j nodes", "<10ms"],
          ["7", "Update conversation metadata", "<10ms"],
        ]}
      />
      <Callout>
        Data is safe in Neo4j after Step 3. Even if embedding fails, the verbatim content is preserved.
        Full-text search remains available as a fallback.
      </Callout>
      <H3>Write Retry</H3>
      <P>
        Neo4j writes retry up to 3 times on transient errors (<InlineCode>ServiceUnavailable</InlineCode>,{" "}
        <InlineCode>SessionExpired</InlineCode>, <InlineCode>TransientError</InlineCode>) with exponential
        backoff (1s, 2s). Non-retryable errors are logged and returned immediately.
      </P>
    </>
  ),

  "retrieval-pipeline": (
    <>
      <H2>Retrieval Pipeline</H2>
      <P>The 8-step query pipeline runs synchronously and targets &lt;500ms end-to-end.</P>
      <Table
        headers={["Step", "Action"]}
        rows={[
          ["1", "Parse date filters (exact date, range, or none)"],
          ["2", "Embed query text via OpenAI (~250ms)"],
          ["3", "Three parallel vector searches: Segment, Message, Chunk indexes"],
          ["4", "Deduplicate by messageId (highest score wins)"],
          ["5", "Apply provider filter if specified"],
          ["6", "Score = similarity * recency_factor"],
          ["7", "Rank and take topK results"],
          ["8", "Assemble verbatim content within tokenBudget"],
        ]}
      />
      <H3>Full-text Fallback</H3>
      <P>
        If no vector results score above 0.70, or if the OpenAI API is unavailable, the system falls back
        to Neo4j full-text (Lucene) search on <InlineCode>index_fulltext_message</InlineCode>. This ensures
        retrieval always works, even without embeddings.
      </P>
      <H3>Recency Scoring</H3>
      <Table
        headers={["Age", "Recency Factor"]}
        rows={[
          ["Today", "1.0"],
          ["7 days", "0.9"],
          ["30 days", "0.7"],
          ["90 days", "0.5"],
          ["365 days", "0.2"],
        ]}
      />
    </>
  ),

  segmentation: (
    <>
      <H2>Segmentation (Hub and Spoke)</H2>
      <P>
        Large conversations are divided into 20-message Segment nodes. Segments are the primary unit
        of vector search -- searching 18 segment embeddings is far faster than scanning 667 individual messages.
      </P>
      <CodeBlock title="hub and spoke model">{`              (:Conversation) ← HUB
                    │
      ┌─────────────┼─────────────┐
      │             │             │
 (:Segment 0)  (:Segment 1)  (:Segment 2) ...
  ~33 messages  ~33 messages   ~33 messages
      │             │             │
 msg0→msg1→...  msg37→msg38→...  msg74→...`}</CodeBlock>
      <H3>Segmentation Rules</H3>
      <Table
        headers={["Rule", "Value"]}
        rows={[
          ["Trigger", "Every 20 messages OR 10,000 tokens (whichever first)"],
          ["Overlap", "Last 2 messages of segment N = first 2 of segment N+1"],
          ["Min messages", "5 per segment"],
          ["Max messages", "50 per segment"],
          ["Content format", "Verbatim concat: USER [ts]: ...\\nASSISTANT [ts]: ..."],
        ]}
      />
      <Callout>
        The 2-message overlap ensures no context is lost at segment boundaries.
        A query that hits near a boundary finds relevant content in either segment.
      </Callout>
    </>
  ),

  "mcp-claude": (
    <>
      <H2>Claude Integration (MCP stdio)</H2>
      <P>
        Claude Desktop connects to Engram via the MCP stdio transport. The server exposes two tools:{" "}
        <InlineCode>memory_write</InlineCode> and <InlineCode>memory_query</InlineCode>.
      </P>
      <H3>Configuration</H3>
      <CodeBlock title="claude_desktop_config.json">{`{
  "mcpServers": {
    "engram": {
      "command": "uv",
      "args": ["run", "engram-mcp"],
      "env": {
        "NEO4J_URI": "bolt://127.0.0.1:7687",
        "NEO4J_USERNAME": "neo4j",
        "NEO4J_PASSWORD": "your-password",
        "OPENAI_API_KEY": "sk-...",
        "MCP_USER_ID": "your-user-id"
      }
    }
  }
}`}</CodeBlock>
      <P>
        The MCP server calls the write and query services directly -- no HTTP round-trip. Tool logic
        is shared with the HTTP server via <InlineCode>mcp_tools.py</InlineCode>.
      </P>
    </>
  ),

  "mcp-chatgpt": (
    <>
      <H2>ChatGPT Integration (MCP HTTP/SSE)</H2>
      <P>
        ChatGPT Apps connect via the MCP Streamable HTTP transport on port 8001.
        This is the <Highlight>production path</Highlight> -- not the temporary Custom GPT REST bridge.
      </P>
      <H3>Running the HTTP server</H3>
      <CodeBlock title="terminal">{`uv run engram-mcp-http
# Endpoint: POST /mcp (Bearer token required)
# Health:   GET /health (no auth)
# Default:  0.0.0.0:8001`}</CodeBlock>
      <H3>Architecture</H3>
      <Table
        headers={["Component", "Detail"]}
        rows={[
          ["Transport", "StreamableHTTPSessionManager (stateless=True)"],
          ["Auth", "BearerAuthMiddleware validates JWT on every /mcp request"],
          ["User isolation", "ContextVar carries user_id per request"],
          ["Tool logic", "Shared via mcp_tools.py -- zero duplication with stdio server"],
          ["Entry point", "engram-mcp-http (pyproject.toml)"],
        ]}
      />
      <Callout>
        The Custom GPT Action (POST /chatgpt/write) is a temporary testing bridge only.
        Do not replicate this REST pattern for other providers. All LLM integrations follow the MCP pattern.
      </Callout>
    </>
  ),

  providers: (
    <>
      <H2>Provider Adapters</H2>
      <P>
        Five provider adapters normalize different LLM conversation formats into Engram&apos;s internal
        Common Message Format. All adapters are pure functions with no I/O.
      </P>
      <Table
        headers={["Provider", "Input Format", "Adapter"]}
        rows={[
          ["ChatGPT", "Chat Completions API + conversations.json", "chatgpt.py"],
          ["Claude", "type=\"message\" API + history[] format", "claude.py"],
          ["Gemini", "generateContent candidates + contents[]", "gemini.py"],
          ["Grok", "OpenAI-compatible format", "grok.py"],
          ["Copilot", "VS Code requests[] + turns[]", "copilot.py"],
        ]}
      />
      <P>
        Message IDs are generated deterministically via SHA-256 hash. System and tool roles are
        filtered out. The <InlineCode>normalizer.py</InlineCode> dispatch layer routes to the correct adapter
        based on the <InlineCode>provider</InlineCode> field.
      </P>
    </>
  ),

  "api-write": (
    <>
      <H2>POST /memory/write</H2>
      <P>Store a conversation verbatim. Returns 202 immediately; processing is async.</P>
      <CodeBlock title="request">{`POST /memory/write
Authorization: Bearer <token>
Content-Type: application/json

{
  "userId": "string",
  "conversationId": "string",
  "provider": "chatgpt" | "claude" | "gemini" | "grok" | "copilot" | "custom",
  "model": "string",
  "messages": [
    {
      "messageId": "string",
      "role": "user" | "assistant",
      "content": "string (verbatim)",
      "timestamp": "ISO 8601",
      "tokenCount": number
    }
  ]
}`}</CodeBlock>
      <CodeBlock title="response">{`202 Accepted
{
  "status": "queued",
  "conversationId": "...",
  "messageCount": 12
}`}</CodeBlock>
      <P>Writes are idempotent. Sending the same conversationId + messageIds twice is a no-op.</P>
    </>
  ),

  "api-query": (
    <>
      <H2>POST /memory/query</H2>
      <P>Semantic retrieval across all stored conversations.</P>
      <CodeBlock title="request">{`POST /memory/query
Authorization: Bearer <token>

{
  "userId": "string",
  "query": "string",
  "topK": 5,
  "tokenBudget": 4000,
  "providers": ["chatgpt", "claude"],  // optional filter
  "date": "2026-03-05",               // optional exact date
  "dateFrom": "2026-03-01",           // optional range start
  "dateTo": "2026-03-10"              // optional range end
}`}</CodeBlock>
      <CodeBlock title="response">{`200 OK
{
  "results": [
    {
      "conversationId": "...",
      "provider": "chatgpt",
      "model": "gpt-4o",
      "similarity": 0.87,
      "messages": [
        { "role": "user", "content": "...", "timestamp": "..." },
        { "role": "assistant", "content": "...", "timestamp": "..." }
      ]
    }
  ],
  "queryLatencyMs": 364,
  "totalResults": 3
}`}</CodeBlock>
    </>
  ),

  "api-conversations": (
    <>
      <H2>GET /memory/conversations</H2>
      <P>Paginated list of stored conversations with optional filters.</P>
      <CodeBlock title="request">{`GET /memory/conversations?limit=10&offset=0&provider=chatgpt&dateFrom=2026-03-01
Authorization: Bearer <token>`}</CodeBlock>
      <H3>GET /memory/conversation/:id</H3>
      <P>Full verbatim read of a single conversation. Returns 404 for not-found and cross-user attempts identically (no information leak).</P>
    </>
  ),

  "api-delete": (
    <>
      <H2>DELETE Endpoints</H2>
      <H3>DELETE /memory/conversation/:id</H3>
      <P>
        Cascade delete: Chunks, Messages, Segments, then Conversation.
        Cross-user attempts return 404. Idempotent (second call returns 404).
      </P>
      <H3>DELETE /memory/user/:userId</H3>
      <P>
        GDPR erasure. 5-step cascade deleting all user data. Returns 403 if path userId
        does not match token userId.
      </P>
    </>
  ),

  "api-auth": (
    <>
      <H2>Authentication</H2>
      <P>All endpoints except <InlineCode>/health</InlineCode> require a JWT Bearer token.</P>
      <H3>Dev tokens</H3>
      <CodeBlock title="terminal">{`curl -X POST http://localhost:8000/auth/token \\
  -H "Content-Type: application/json" \\
  -d '{"userId": "your-user-id"}'`}</CodeBlock>
      <Callout>
        POST /auth/token is dev-only and blocked when <InlineCode>is_production=true</InlineCode>.
        It must be removed before production deployment.
      </Callout>
      <H3>API keys (long-lived)</H3>
      <CodeBlock title="terminal">{`curl -X POST http://localhost:8000/auth/apikey \\
  -H "Content-Type: application/json" \\
  -d '{"userId": "your-user-id"}'
# Returns a 1-year JWT for third-party integrations`}</CodeBlock>
      <P>
        JWT uses HS256 with the <InlineCode>JWT_SECRET_KEY</InlineCode> environment variable.
        Rate limiting is per-user, decoded from the JWT.
      </P>
    </>
  ),

  performance: (
    <>
      <H2>Performance</H2>
      <P>Measured on local Neo4j (bolt://127.0.0.1:7687), OpenAI enabled, Redis disabled.</P>
      <Table
        headers={["Operation", "Mean", "P95", "Notes"]}
        rows={[
          ["HTTP write (202 async)", "31.9ms", "24.1ms", "Just HTTP overhead -- real work is async"],
          ["MCP write (sync await)", "331.2ms", "311.7ms", "True cost: Neo4j + OpenAI embedding"],
          ["HTTP vector query", "364.6ms", "532.4ms", "Dominated by OpenAI embedding call"],
          ["HTTP fulltext query", "263.6ms", "153.1ms", "Skips OpenAI -- pure Neo4j Lucene"],
          ["MCP query (vector)", "410.7ms", "674.2ms", "Same pipeline, in-process"],
        ]}
      />
      <Callout>
        The single biggest optimization lever is Redis. With embedding cache enabled, repeated
        queries drop from ~360ms to ~120ms by skipping the OpenAI round-trip.
      </Callout>
    </>
  ),

  roadmap: (
    <>
      <H2>Roadmap</H2>
      <Table
        headers={["Phase", "Description", "Status"]}
        rows={[
          ["0", "Neo4j schema, constraints, indexes", "Complete"],
          ["1", "Write API, segmentation, JWT auth", "Complete"],
          ["2", "Embedding pipeline, semantic search, query API", "Complete"],
          ["3", "Read/delete API, provider adapters, rate limiting, observability", "Complete"],
          ["4", "MCP server (Claude Desktop), write retry + backoff", "Complete"],
          ["5A", "ChatGPT Custom GPT Action + MCP HTTP/SSE server", "Complete"],
          ["5B", "Gemini MCP server", "Pending"],
          ["5C", "Grok, Copilot MCP servers", "Pending"],
          ["6", "Production hardening (Docker, Prometheus, AuraDB)", "Partial"],
          ["7", "Semantic fact versioning (UPDATED_TO graph)", "Designed, parked"],
        ]}
      />
      <H3>Phase 7: Semantic Fact Versioning</H3>
      <P>
        Fully designed and parked. When a new message updates an existing fact, the system detects it
        via cosine similarity and creates a chainable <InlineCode>UPDATED_TO</InlineCode> relationship.
        Old messages are marked <InlineCode>superseded</InlineCode> but never deleted.
      </P>
      <CodeBlock title="version chain">{`(:Message {content: "I like blue", status: "superseded"})
  -[:UPDATED_TO]->
(:Message {content: "I like red", status: "superseded"})
  -[:UPDATED_TO]->
(:Message {content: "I like green", status: "current"})`}</CodeBlock>
    </>
  ),
};

// ─── Main Page ──────────────────────────────────────────────────

export default function DocsPage() {
  const [activeSection, setActiveSection] = useState("overview");

  return (
    <>
      <Nav />
      <div className="flex min-h-screen pt-16">
        {/* Sidebar */}
        <aside className="hidden lg:block w-64 shrink-0 border-r border-[rgba(255,255,255,0.05)] bg-[rgba(3,0,20,0.6)] backdrop-blur-sm">
          <div className="sticky top-16 p-6 space-y-6 max-h-[calc(100vh-4rem)] overflow-y-auto">
            {SIDEBAR.map((group) => (
              <div key={group.group}>
                <p className="text-[10px] font-mono tracking-[0.2em] uppercase text-[#5D5A73] mb-2">
                  {group.group}
                </p>
                <div className="space-y-0.5">
                  {group.items.map((item) => (
                    <button
                      key={item.id}
                      onClick={() => setActiveSection(item.id)}
                      className={`block w-full text-left px-3 py-1.5 rounded-md text-xs transition-colors ${
                        activeSection === item.id
                          ? "bg-[rgba(224,159,62,0.1)] text-[#E09F3E] font-medium"
                          : "text-[#7E7B93] hover:text-[#E2DFD6] hover:bg-[rgba(255,255,255,0.03)]"
                      }`}
                    >
                      {item.label}
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </aside>

        {/* Mobile sidebar toggle + dropdown */}
        <div className="lg:hidden fixed bottom-6 right-6 z-50">
          <MobileSidebarMenu
            activeSection={activeSection}
            onSelect={setActiveSection}
          />
        </div>

        {/* Content */}
        <main className="flex-1 min-w-0 px-6 sm:px-10 lg:px-16 py-10 max-w-3xl">
          <AnimatePresence mode="wait">
            <motion.div
              key={activeSection}
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
              transition={{ duration: 0.25 }}
            >
              {CONTENT[activeSection] ?? (
                <P>Section not found.</P>
              )}
            </motion.div>
          </AnimatePresence>
        </main>
      </div>
    </>
  );
}

// ─── Mobile sidebar as a floating menu ──────────────────────────

function MobileSidebarMenu({
  activeSection,
  onSelect,
}: {
  activeSection: string;
  onSelect: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);

  return (
    <>
      <button
        onClick={() => setOpen(!open)}
        className="w-12 h-12 rounded-full bg-[#1C1B22] border border-[rgba(255,255,255,0.1)] shadow-lg flex items-center justify-center text-[#E09F3E]"
      >
        <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          {open ? (
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
          ) : (
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
          )}
        </svg>
      </button>
      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ opacity: 0, y: 10, scale: 0.95 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 10, scale: 0.95 }}
            className="absolute bottom-14 right-0 w-56 max-h-80 overflow-y-auto rounded-xl border border-[rgba(255,255,255,0.08)] bg-[#13121A] shadow-2xl p-3 space-y-3"
          >
            {SIDEBAR.map((group) => (
              <div key={group.group}>
                <p className="text-[9px] font-mono tracking-[0.2em] uppercase text-[#5D5A73] mb-1 px-2">
                  {group.group}
                </p>
                {group.items.map((item) => (
                  <button
                    key={item.id}
                    onClick={() => {
                      onSelect(item.id);
                      setOpen(false);
                    }}
                    className={`block w-full text-left px-2 py-1.5 rounded text-xs transition-colors ${
                      activeSection === item.id
                        ? "text-[#E09F3E] font-medium"
                        : "text-[#7E7B93] hover:text-[#E2DFD6]"
                    }`}
                  >
                    {item.label}
                  </button>
                ))}
              </div>
            ))}
          </motion.div>
        )}
      </AnimatePresence>
    </>
  );
}
