# Cross-LLM Memory Service — Implementation Log

> **Purpose**: Canonical record of what was built, what was broken, and what was fixed in each phase. Intended as a reference for future development and onboarding.

---

## Phase 0 — Schema & Infrastructure

### Goal
Establish the Neo4j graph schema, project scaffolding, and tooling before any application code is written.

### What Was Implemented

#### Project Scaffolding
- `pyproject.toml` — project definition using `hatchling` build backend, Python ≥ 3.11, `uv` as package manager
- Source layout: `src/memory/` package with submodules for API, auth, config, db, models, services
- Dev dependencies: `pytest`, `pytest-asyncio`, `httpx`, `anyio`

#### Configuration (`src/memory/config.py`)
- `Settings(BaseSettings)` backed by `pydantic-settings`, reads from `.env`
- Fields: `neo4j_uri`, `neo4j_username`, `neo4j_password`, `neo4j_database`, connection pool size, timeout, JWT settings, OpenAI API key (Phase 2 placeholder), Redis URL (Phase 2 placeholder)
- Properties: `is_development`, `is_production`

#### Database Connection (`src/memory/db/connection.py`)
- Sync driver singleton (`get_driver` / `close_driver` / `verify_connectivity`) — used by scripts
- Async driver singleton (`get_async_driver` / `close_async_driver` / `verify_async_connectivity`) — used by FastAPI
- Both configured with pool size and connection timeout from settings

#### Schema Setup (`scripts/setup_schema.py`)
Applies 16 DDL statements to Neo4j, all using `IF NOT EXISTS` (fully idempotent).
A pre-flight step drops `index_segment_conversation_order` if it exists in the database — that plain index covers the same columns as `constraint_segment_conversation_index`, and Neo4j will refuse to create the constraint while the plain index is present (see Bug P0-3 below).

| # | Name | Type | Purpose |
|---|------|------|---------|
| 1 | `constraint_user_id` | Unique constraint | `User.userId` |
| 2 | `constraint_conversation_id` | Unique constraint | `Conversation.conversationId` |
| 3 | `constraint_message_id` | Unique constraint | `Message.messageId` |
| 4 | `constraint_segment_id` | Unique constraint | `Segment.segmentId` |
| 5 | `constraint_chunk_id` | Unique constraint | `Chunk.chunkId` |
| 6 | `constraint_segment_conversation_index` | Composite unique constraint | `(Segment.conversationId, Segment.segmentIndex)` — prevents duplicate segments; its backing index also serves as the segment ordering index *(added during Phase 1 review)* |
| 7 | `index_conversation_user_date` | Composite index | `(Conversation.userId, Conversation.startedAt)` — date pre-filter |
| 8 | `index_message_user_timestamp` | Composite index | `(Message.userId, Message.timestamp)` |
| 9 | `index_message_conversation` | Index | `Message.conversationId` |
| 10 | `index_segment_conversation` | Index | `Segment.conversationId` |
| 11 | `index_chunk_message` | Index | `Chunk.messageId` |
| 12 | `index_message_conversation_index` | Composite index | `(Message.conversationId, Message.messageIndex)` — segment range queries *(added during Phase 1 review)* |
| 13 | `index_vector_segment` | Vector index | `Segment.embedding` (1536 dims, cosine) — Phase 2 |
| 14 | `index_vector_message` | Vector index | `Message.embedding` (1536 dims, cosine) — Phase 2 |
| 15 | `index_vector_chunk` | Vector index | `Chunk.embedding` (1536 dims, cosine) — Phase 2 |
| 16 | `index_fulltext_message` | Full-text index | `Message.content` — keyword fallback search |

#### Schema Verification (`scripts/verify_schema.py`)
- Reads `SHOW CONSTRAINTS` and `SHOW INDEXES` from Neo4j
- Checks existence and type of each expected schema object
- Prints a pass/fail table using `rich`

#### Sample Data Seeding (`scripts/seed_sample_data.py`)
- Creates representative User, Conversation, Message, and Segment nodes
- Demonstrates the verbatim storage model with real-looking conversation data

### Phase 0 Bugs Encountered & Fixed

#### Bug P0-1 — `verify_schema.py` reported all constraints as `state=N/A`
- **Root cause**: Neo4j 2026.x removed the `state` column from `SHOW CONSTRAINTS`. Constraints are either present or absent — there is no intermediate `ONLINE`/`POPULATING` state for constraints.
- **Fix**: Rewrote constraint verification to check existence and type only. Removed all `state == "ONLINE"` checks.

#### Bug P0-2 — `seed_sample_data.py` raised `TypeError` on full-text search
- **Root cause**: `session.run(QUERY, query=query_text)` — the keyword argument name `query` collided with the neo4j driver's own `session.run(query=...)` parameter.
- **Fix**: Renamed the Cypher parameter from `$query` to `$searchText` and the keyword argument accordingly.

#### Bug P0-3 — `constraint_segment_conversation_index` could not be created while a regular index covered the same columns
- **Root cause**: `index_segment_conversation_order` (a plain composite index on `Segment.conversationId, Segment.segmentIndex`) was applied to the database before `constraint_segment_conversation_index` (a composite uniqueness constraint on the same two properties) was added to the schema. Neo4j requires that no existing index covers the constraint columns — if one is present, constraint creation raises `Neo.ClientError.Schema.IndexAlreadyExists` and the statement is skipped. Because `setup_schema.py` uses `IF NOT EXISTS` (which suppresses `AlreadyExists` errors for constraints, not for this conflict), the constraint was silently absent from the database even though setup reported success.
- **Impact**: Without the constraint, the MERGE-based duplicate-segment prevention (Bug P1-5 fix) provided no database-level safety guarantee. Concurrent writes to the same conversation could still create two Segment nodes at the same `segmentIndex`.
- **Fix**:
  1. Removed `index_segment_conversation_order` from `SCHEMA_STATEMENTS` entirely. The constraint's own backing index covers `(conversationId, segmentIndex)` and provides equivalent query performance — the separate index is redundant.
  2. Added a pre-flight `DROP INDEX index_segment_conversation_order IF EXISTS` step at the top of `main()` in `setup_schema.py`. This handles existing databases that already have the plain index without manual intervention.
  3. Removed `index_segment_conversation_order` from the expected set in `test_connection.py::test_neo4j_composite_indexes_exist` (the constraint's backing index has the constraint name, not the old index name).

---

## Phase 1 — Write API

### Goal
Build the HTTP write pipeline: accept conversation batches in Canonical Message Format (CMF), return `202 Accepted` immediately, and persist everything verbatim to Neo4j as a background task.

### What Was Implemented

#### Data Models (`src/memory/models/`)

**`message.py` — `MessageIn`**
```
messageId   : str        (min 1 char)
role        : "user" | "assistant"
content     : str        (min 1 char)
timestamp   : AwareDatetime  (timezone-aware, Pydantic v2)
tokenCount  : int        (≥ 0)
```

**`requests.py`**
- `WriteRequest` — full conversation payload; validates `provider` against a `ProviderType` Literal (`chatgpt`, `claude`, `gemini`, `grok`, `copilot`)
- `WriteResponse` — `{ status: "accepted", messageCount, conversationId }`
- `TokenRequest` / `TokenResponse` — dev-only token issuance
- `HealthResponse`

#### Auth (`src/memory/auth/jwt_handler.py`)
- `create_access_token(user_id)` — issues a signed JWT with `sub`, `iat`, `exp`
- `decode_access_token(token)` — verifies signature and expiry, returns `sub`
- Algorithm: HS256; secret and TTL from settings

#### FastAPI Application (`src/memory/api/main.py`)
- `@asynccontextmanager lifespan` — creates the async Neo4j driver on startup, verifies connectivity (fails fast), closes explicitly on shutdown (Neo4j 6.x requires explicit close — `__del__` no longer closes)
- CORS: permissive in development, locked down in production
- Routers: `health`, `auth`, `memory`

#### Routes
- `GET /health` — checks Neo4j connectivity, returns `{ status: "ok", neo4j: "connected" }`
- `POST /auth/token` — DEV ONLY; issues a JWT; blocked in production via `settings.is_production`
- `POST /memory/write` — validates JWT, checks `userId` matches token, enqueues background write, returns `202` immediately

#### Write Service (`src/memory/services/write_service.py`)
Pipeline executed as a `BackgroundTask`:
1. MERGE `User` node (create or update `lastActiveAt`)
2. MERGE `Conversation` node + link to User
3. Atomic message write (see details below)
4. Increment `Conversation.messageCount` by actual new messages only
5. Trigger segmentation check

#### Segment Service (`src/memory/services/segment_service.py`)
Hub-and-spoke segmentation model:
- One `Segment` per 20 messages (`SEGMENT_SIZE = 20`)
- Segment content = verbatim concatenation: `ROLE [timestamp]: content\n...`
- `Segment` linked to `Conversation` via `HAS_SEGMENT`
- Each `Message` linked to its `Segment` via `CONTAINS_MESSAGE {order}`
- Consecutive segments linked via `NEXT_SEGMENT`

### Phase 1 Bugs Encountered & Fixed

---

#### Bug P1-1 — Test expected 403 for missing auth, got 401

- **Root cause**: FastAPI's `HTTPBearer` dependency returns `HTTP 401 Unauthorized` when the `Authorization` header is absent or malformed. `HTTP 403 Forbidden` is the correct code when a user is *authenticated but not authorised* (e.g., `userId` mismatch). The test had confused the two.
- **Fix**: Changed test assertion from `assert resp.status_code == 403` to `assert resp.status_code == 401` for both the missing-token and invalid-token cases.

---

#### Bug P1-2 — Idempotent resend created 4 messages instead of 2

- **Root cause**: The `HAS_MESSAGE` relationship was created with an `order` property: `MERGE (c)-[:HAS_MESSAGE {order: $messageIndex}]->(m)`. On a resend, the second write computed `messageIndex = 2` (because 2 messages already existed), so `{order: 2}` didn't match the existing `{order: 0}` relationship. Neo4j's MERGE saw no match and created a **second** `HAS_MESSAGE` relationship pointing to the same `Message` node — making the conversation appear to have 4 messages.
- **Fix**: Removed `{order: $messageIndex}` from the `HAS_MESSAGE` relationship pattern. `messageIndex` belongs on the `Message` node itself (`m.messageIndex`), not on the relationship. The relationship carries no properties: `MERGE (c)-[:HAS_MESSAGE]->(m)`.

---

#### Bug P1-3 — TOCTOU race condition on messageIndex assignment

- **Root cause**: `_get_message_count_tx` executed inside `session.execute_read()` and returned `current_count` to Python. `_write_messages_tx` then ran in a **separate** `session.execute_write()`. Under Neo4j's MVCC, two concurrent write transactions for the same conversation could both read `current_count = N` before either wrote anything, then both assign indices `N, N+1, N+2...` — producing colliding `messageIndex` values on different `Message` nodes.
- **Why a simple patch was insufficient**: Moving the read *into* the write transaction via a plain `MATCH ... RETURN count(m)` still does not prevent the race, because Neo4j MVCC allows concurrent write transactions to both read the same snapshot count before either acquires a write lock.
- **Fix**: At the start of `_write_messages_tx`, issue `SET c.updatedAt = $now` **before** reading the message count. `SET` on a node forces Neo4j to acquire an exclusive write lock on the `Conversation` node. Any concurrent transaction attempting the same `SET` blocks until the first commits. After the first commits, the second reads the updated count — serialising index assignment correctly.

---

#### Bug P1-4 — `Conversation.messageCount` inflated on duplicate sends

- **Root cause**: `_merge_conversation_tx` used `ON MATCH SET c.messageCount = c.messageCount + $incomingCount`. This blindly added the incoming batch size on every call regardless of how many messages were actually new. On a full resend where all `messageId`s already existed, MERGE created no new Message nodes, but `messageCount` was still incremented by the full batch size.
- **Why a simple patch was insufficient**: Simply moving the increment elsewhere still requires knowing the *actual* new count, not the incoming count.
- **Fix**:
  1. `_merge_conversation_tx` sets `messageCount = 0` on `ON CREATE` and does **not touch** `messageCount` on `ON MATCH`.
  2. `_write_messages_tx` sets `m._isNew = true` in `ON CREATE SET` for each newly created `Message` node.
  3. After the UNWIND, collect all `(messageId, isNew)` records and count `isNew == true` entries → `actual_new_count`.
  4. A new `_bump_conversation_count_tx` increments `c.messageCount` by `actual_new_count` only. Called only when `actual_new_count > 0`.
  5. The `_isNew` sentinel is cleaned up in the same transaction via a second UNWIND.

---

#### Bug P1-5 — Duplicate Segment nodes on concurrent writes

- **Root cause**: `_create_segment_tx` used `CREATE (s:Segment {...})`, generating a new `segmentId` (UUID) on every call. Two concurrent writes to the same conversation could both pass the `complete_segments_needed > existing_segments` check, then both `CREATE` a Segment at the same `segmentIndex` with different UUIDs. The `constraint_segment_id` uniqueness constraint on `segmentId` did not prevent this because each call used a different UUID.
- **Why a simple patch was insufficient**: Adding a guard check ("does this segment already exist?") before `CREATE` still has a TOCTOU window between the check and the create. The only safe fix is a database-enforced constraint combined with `MERGE`.
- **Fix**:
  1. Added `constraint_segment_conversation_index`: composite uniqueness constraint on `(Segment.conversationId, Segment.segmentIndex)` in `setup_schema.py`.
  2. Changed `CREATE` to `MERGE (s:Segment {conversationId: $conversationId, segmentIndex: $segIndex}) ON CREATE SET ...`. The MERGE serialises on the composite key — the second concurrent call matches the already-created node and executes no `ON CREATE`, leaving the segment unchanged.

---

#### Bug P1-6 — N+1 Cypher queries for message writes and CONTAINS_MESSAGE links

- **Root cause**: Both `_write_messages_tx` (one `tx.run()` per message) and the `CONTAINS_MESSAGE` loop in `_create_segment_tx` (one `tx.run()` per message per segment) issued O(N) separate round-trips inside a single transaction. For a 20-message segment this meant ~40 Cypher calls per write.
- **Fix**: Replaced both loops with `UNWIND`-based batch queries:
  - Message writes: one `tx.run()` with `UNWIND $messages AS msg MERGE (m:Message ...) ON CREATE SET ...`
  - NEXT_MESSAGE chain: one `tx.run()` with `UNWIND $pairs AS pair MERGE (a)-[:NEXT_MESSAGE]->(b)`
  - CONTAINS_MESSAGE links: one `tx.run()` with `UNWIND $messageItems AS item MERGE (s)-[:CONTAINS_MESSAGE {order: item.order}]->(m)`
  - Result: 5 `tx.run()` calls per write batch (lock+count, UNWIND merge, chain, cross-batch link, sentinel cleanup) vs ~40 previously.

---

#### Bug P1-7 — `Conversation.segmentCount` driven by unreliable increment

- **Root cause**: `SET c.segmentCount = c.segmentCount + 1` was called inside `_create_segment_tx`. With the `CREATE`→`MERGE` fix, a second concurrent call matches the existing Segment and does not execute `ON CREATE`, but **does** still reach the `segmentCount + 1` line — incrementing an already-correct counter. Additionally, if a prior write had failed mid-transaction leaving the counter in an incorrect state, it could never self-correct.
- **Fix**: Replaced the increment with a direct count of actual `Segment` nodes:
  ```cypher
  MATCH (c:Conversation {conversationId: $convId})
  OPTIONAL MATCH (c)-[:HAS_SEGMENT]->(s:Segment)
  WITH c, count(s) AS actualCount
  SET c.segmentCount = actualCount
  ```
  This is always accurate regardless of prior state or concurrent writes.

---

#### Bug P1-8 — Backwards NEXT_MESSAGE edge on idempotent resend

- **Root cause**: The "connect first new message to last existing message" step used `base_offset` (the total message count at the start of the transaction) as the index of the previous message. On a resend, `base_offset` included the re-sent messages themselves, so `base_offset - 1` pointed to the *last* message in the re-sent batch rather than the last message *before* the batch. This caused a backward `last→first` edge to be MERGEd in addition to the correct `first→second` chain.
- **Fix**: The chain-connection step is gated on whether `messages[0]` is actually new, determined by checking if its `messageId` is in `new_ids` (the set of IDs that had `_isNew = true` after the UNWIND). If `messages[0]` already existed, no chain-connection attempt is made.

---

#### Bug P1-9 — Test cleanup using the global driver singleton

- **Root cause**: The `cleanup_test_data` fixture in `test_write_api.py` called `get_driver()` (the module-level singleton from `connection.py`) then `close_driver()` after tests completed. This set `_sync_driver = None`, coupling test teardown to the application's driver lifecycle. Any test module running after this one would silently re-create the singleton.
- **Fix**: The cleanup fixture now creates its own isolated `GraphDatabase.driver(...)` using settings credentials and closes it in a `finally` block. It never touches the application singleton.

---

### Phase 1 — Test Coverage Summary

| Class | Tests | What Is Covered |
|-------|-------|----------------|
| `TestHealth` | 1 | `/health` endpoint, Neo4j connectivity |
| `TestAuthToken` | 2 | Token issuance success, missing `userId` rejection |
| `TestWriteValidation` | 7 | Missing auth (401), invalid token (401), `userId` mismatch (403), empty messages, invalid role, invalid provider, missing content |
| `TestWriteSuccess` | 8 | 202 response, response body shape, User node creation, Conversation node creation, verbatim message storage, NEXT_MESSAGE chain, User→Conversation relationship, idempotent duplicate send |
| `TestMessageCountAccuracy` | 3 | Correct count on first write, no inflation on duplicate send *(P1-4 regression)*, correct increment on append |
| `TestAppendBehavior` | 4 | Sequential indices across batches, NEXT_MESSAGE chain spanning batch boundary, no backwards edges on resend *(P1-8 regression)*, unique indices across two writes |
| `TestSegmentation` | 7 | No segment below threshold, segment at threshold, verbatim segment content, two segments at 40 messages with NEXT_SEGMENT chain, no duplicate segment on resend *(P1-5 regression)*, `segmentCount` accuracy *(P1-7 regression)*, segment triggered by threshold crossing across two writes, all 20 CONTAINS_MESSAGE links present *(P1-6 regression)* |
| **Total** | **34** | |

---

## Phase 2 — Embedding Pipeline & Semantic Query

### Goal
Add a semantic retrieval path: embed conversations using OpenAI `text-embedding-3-small`, store vectors in Neo4j, and expose `POST /memory/query` for ranked, token-budget-aware memory retrieval. Full-text keyword search is always available as a fallback. Zero hallucination risk — the pipeline never calls a generative model.

### What Was Implemented

#### New Models (`src/memory/models/requests.py`)
- `QueryRequest` — validated query payload with date filtering, topK, tokenBudget, optional provider filter
  - `date` and `dateFrom/dateTo` are mutually exclusive (Pydantic `model_validator`)
  - `effective_date_from` / `effective_date_to` properties resolve both forms to a canonical date range
- `MessageResult` — verbatim message turn (messageId, role, content, timestamp, tokenCount)
- `ConversationResult` — grouped messages from one conversation with relevance score
- `QueryResponse` — full response: results, totalResults, tokenCount, queryLatencyMs, dateFilterApplied, searchMode

#### Embedding Service (`src/memory/services/embedding_service.py`)
Complete vectorisation pipeline:
- `embed_new_content(driver, openai_client, redis_client, conversation_id)` — called as final step of write BackgroundTask; no-op if `openai_client` is None
- `get_query_embedding(openai_client, redis_client, query_text)` — used by query pipeline; returns None on failure (never raises)
- **Idempotency**: only embeds nodes where `embedding IS NULL` — safe to retry
- **Caching**: Redis keyed by `SHA-256(content)`, TTL 3600s; fully optional (graceful degradation if Redis absent or unreachable)
- **Long content handling**: content > 8191 tokens → head+tail averaging (first 4096 + last 4096 tokens), then L2-normalise → deterministic, no generative model involved
- **Chunk nodes**: messages > 512 tokens → 512-token windows with 100-token overlap → `Chunk` nodes linked via `HAS_CHUNK`; chunk vector search always returns the parent `Message`, never the chunk itself
- **Token counting**: `tiktoken.get_encoding("cl100k_base")` — exact token counts matching OpenAI's tokeniser

#### Query Service (`src/memory/services/query_service.py`)
8-step retrieval pipeline:
1. **Date pre-filter** (parallel with step 2) — query `(Conversation.userId, Conversation.startedAt)` composite index; returns candidate `conversationId` set; empty set + date filter = early return (zero results)
2. **Query embedding** (parallel with step 1) — embed query text; `None` if OpenAI unavailable
3. **Three parallel vector searches** — separate Neo4j sessions for Segment, Message, and Chunk indexes via `asyncio.gather`; oversampling `topK × 10` before `userId` post-filter (MVCC limitation workaround)
4. **Deduplicate** by `messageId`, keep highest cosine score
5. **Context expansion** — fetch ±2 neighbours per matched message via `messageIndex` range
6. **Ranking** — two modes:
   - No date filter: `0.7×cosine + 0.2×recency + 0.1×context_bonus`
   - Date filter: `0.8×cosine + 0.1×date_match + 0.1×context_bonus`
   - Recency: piecewise linear decay (0d→1.0, 7d→0.9, 30d→0.7, 90d→0.5, 365+d→0.2)
7. **Token-budget assembly** — skip messages that would exceed budget; never truncates mid-message; group by conversation
8. **Return** `QueryResponse` with `searchMode: "vector" | "fulltext" | "empty"`
- **Full-text fallback**: triggered when no vector hits ≥ 0.70 threshold, or when OpenAI is unavailable

#### Write Service (`src/memory/services/write_service.py`) — Updated
- Signature extended: `write_conversation_to_graph(driver, request, openai_client=None, redis_client=None)`
- Step 7 added (after segmentation): `await embed_new_content(...)` — completely optional; verbatim storage (Phase 1) is already committed before this runs

#### API Layer Updates
- `src/memory/api/main.py` — lifespan now initialises three clients: Neo4j (required, fail-fast), OpenAI (optional if `OPENAI_API_KEY` set), Redis (optional if `REDIS_URL` set); shutdown closes all open connections
- `src/memory/api/dependencies.py` — added `get_openai_client`, `get_redis_client` accessors + `OpenAIClient`, `RedisClient` type aliases
- `src/memory/api/routes/memory.py` — write route now injects and forwards `openai_client` and `redis_client` to background task
- `src/memory/api/routes/query.py` — new file; `POST /memory/query`; same JWT + userId-match guard as write route

---

### Phase 2 Bugs Encountered & Fixed

#### Bug P2-1 — `_embed_long_or_short` referenced but not defined

- **Root cause**: In `_embed_segments`, the loop called `await _embed_long_or_short(...)` — a function name that was planned during design but never written. The long/short branching for segments is already handled inside `_call_openai_embedding` (which checks token count and delegates to `_head_tail_embedding` when needed). The named helper was a dead reference.
- **Fix**: Replaced `_embed_long_or_short(openai_client, redis_client, text=..., token_count=...)` with `_get_or_compute_embedding(openai_client, redis_client, content)`. The `token_count` variable was also removed as it became unused.

#### Bug P2-2 — Cypher `NOT IN` syntax rejected by Neo4j 2026.x

- **Root cause**: The context expansion query used `AND neighbor.messageId NOT IN $messageIds`. Neo4j 2026.x (Bolt 6.x) no longer accepts `NOT IN` as a compound infix operator in a `WHERE` clause.
- **Fix**: Changed to `AND NOT (neighbor.messageId IN $messageIds)`, which is valid in all supported Neo4j versions.

#### Bug P2-3 — `all()` predicate removed in Neo4j 2026.x

- **Root cause**: `test_long_message_produces_chunk_nodes` used `all(ch.embedding IS NOT NULL)` in a `RETURN` clause. The `all()` list predicate function was removed in Neo4j 2026.x.
- **Fix** (test-only): Replaced with `count(ch.embedding) = count(ch) AS allEmbedded`. `count(property)` counts non-null values; comparing it to `count(node)` (which counts all) is equivalent.

#### Bug P2-4 — Date filter with zero matches not returning empty results

- **Root cause**: When `_date_prefilter_tx` returned an empty list (no conversations matched the date range), `_candidate_filter_clause` returned an empty string (no filter), so the vector searches ran against **all** conversations for that user, ignoring the date filter entirely.
- **Fix**: After `asyncio.gather(candidate_ids_task, embed_task)`, added an early-return guard: if `request.has_date_filter and len(candidate_ids) == 0`, immediately return an empty `QueryResponse(searchMode="empty")`.

---

### Phase 2 — Test Coverage Summary

**`tests/test_embedding_pipeline.py`** (14 tests, 1 skipped)

| Class | Tests | What Is Covered |
|-------|-------|----------------|
| `TestTokenUtilities` | 4 | `_count_tokens` accuracy, empty string, single-chunk short text, multi-chunk overlap |
| `TestEmbeddingComputation` | 8 (1 skip) | No-op without OpenAI client, segment embedding stored in Neo4j, short message embedding on Message node, long message → Chunk nodes (not Message.embedding), Redis cache hit prevents duplicate API calls *(skipped: Redis not in CI)*, query embedding dimensionality, None on API error, idempotency (second call makes no new API calls) |
| `TestHeadTailEmbedding` | 2 | Head+tail triggered for oversized content (one batched API call with 2 texts), result is L2-normalised |

**`tests/test_query_api.py`** (33 tests)

| Class | Tests | What Is Covered |
|-------|-------|----------------|
| `TestQueryAuth` | 4 | Missing token (401/403), invalid token (401), userId mismatch (403), valid token succeeds |
| `TestQueryValidation` | 5 | Empty query, `date` + `dateFrom` mutual exclusivity, `dateTo` without `dateFrom`, invalid date format, topK out of range |
| `TestVectorSearch` | 6 | Quantum query → conv A; cooking query → conv B; response structure; verbatim content; `searchMode="vector"`; no cross-user data leakage |
| `TestFulltextFallback` | 3 | Fulltext used when OpenAI unavailable; fulltext triggered by zero vector hits below threshold; `searchMode="empty"` when nothing matches |
| `TestDateFilter` | 4 | Out-of-range conversation excluded; single-day `date` filter; `dateFilterApplied=False` without filter; empty results when date range matches nothing *(P2-4 regression)* |
| `TestTokenBudget` | 3 | Budget caps tokenCount; messages never truncated mid-content; larger budget returns at least as many messages |
| `TestRanking` | 3 | Results ordered score-descending; scores in \[0,1\]; date-filter and no-filter modes produce different scores |
| `TestContextExpansion` | 1 | Neighbour messages appear alongside direct match |
| `TestResponseShape` | 4 | ConversationResult fields present; MessageResult fields and types; latency > 0; tokenCount == sum of message tokenCounts |

---

---

## Pre-Phase 3 Audit — Cross-Phase Review Findings

Conducted after Phases 0, 1, and 2 were complete. The goal was to verify the three phases function correctly as a connected system. The review found bugs that exist in live code but are silent — no test fails, no exception is raised, no error is logged.

### Priority classification

| ID | Severity | Status | Description |
|----|----------|--------|-------------|
| P3-PRE-1 | **P0 — Fix before Phase 3** | ✅ Fixed | `totalTokens` inflates on every duplicate send |
| P3-PRE-2 | **P0 — Fix before Phase 3** | ✅ Fixed | Long-message chunking re-embeds on every subsequent write |
| P3-PRE-3 | **P0 — Fix before Phase 3** | ✅ Fixed | `providers` filter silently ignored in query pipeline |
| P3-PRE-4 | **P1 — Fix before Phase 3** | ✅ Fixed | Settings/constants disconnect: embedding model hardcoded instead of read from `settings` |
| P3-PRE-5 | **P1 — Fix before Phase 3** | ✅ Fixed | Redis URL defaults to non-empty string; always attempts connection |
| P3-PRE-6 | **P1 — Fix before Phase 3** | ✅ Fixed | `constraint_segment_conversation_index` missing from `test_connection.py` |
| P3-PRE-7 | **P1 — Fix before Phase 3** | ✅ Fixed | No end-to-end write→embed→query integration test |
| P3-PRE-8 | **P2 — Fix during Phase 3** | ⬜ Pending | Version drift: `__init__.py` reports `0.1.0`, `main.py` declares `0.2.0` |
| P3-PRE-9 | **P2 — Fix during Phase 3** | ⬜ Pending | JWT secret key has no production-safety validator in `Settings` |
| P3-PRE-10 | **P3 — Technical debt** | ⬜ Pending | Dead async driver singleton in `db/connection.py` never used by FastAPI |
| P3-PRE-11 | **P3 — Technical debt** | ⬜ Pending | `_candidate_filter_clause` hardcodes alias `node`; breaks on future search paths |

---

### Bug P3-PRE-1 — `Conversation.totalTokens` inflates on every duplicate send

- **Root cause**: `_merge_conversation_tx` uses `ON MATCH SET c.totalTokens = c.totalTokens + $incomingTokens`. `incomingTokens` is the sum of ALL tokens in the incoming batch, including tokens for messages that already exist. Unlike `messageCount` (which was fixed in P1-4 using the `_isNew` sentinel), `totalTokens` was never given the same treatment.
- **Impact**: After N resends of the same conversation, `totalTokens = N × actual_token_count`. Corrupts billing, analytics, and any downstream feature that reads this field.
- **Fix**: Extend `_write_messages_tx` to sum `tokenCount` over `new_ids` only, returning `(actual_new_count, actual_new_tokens)`. Pass `actual_new_tokens` to `_bump_conversation_count_tx` and also increment `c.totalTokens` there. Remove `totalTokens` from the `ON MATCH SET` in `_merge_conversation_tx`.

---

### Bug P3-PRE-2 — Long-message chunking path is not idempotent

- **Root cause**: `_get_unembedded_messages_tx` filters with `WHERE m.embedding IS NULL`. For messages longer than `CHUNK_THRESHOLD` tokens, the design intentionally stores `Message.embedding = NULL` and creates Chunk nodes instead. This means every subsequent call to `embed_new_content` re-fetches those long messages (still `NULL`), recomputes chunk embeddings via OpenAI, and overwrites existing chunk embeddings on MERGE MATCH.
- **Impact**: Every write to a conversation containing long messages makes redundant OpenAI API calls and wastes quota. The `test_embed_new_content_idempotent` test uses a 4-token message and does not catch this.
- **Fix**: Add `AND NOT EXISTS { MATCH (m)-[:HAS_CHUNK]->(:Chunk) }` to the WHERE clause in `_get_unembedded_messages_tx`. Add a dedicated test: long message, call `embed_new_content` twice, verify OpenAI is called exactly once.

---

### Bug P3-PRE-3 — `providers` filter accepted but never applied

- **Root cause**: `QueryRequest.providers` is validated and present in the API contract. No search path in `query_service.py` applies it — not the segment search, message search, chunk search, fulltext fallback, or date pre-filter.
- **Impact**: A client sending `providers=["chatgpt"]` receives results from all providers. The API contract is broken silently.
- **Fix**: Add `AND (m.provider IN $providers OR $providers IS NULL)` (or equivalent) to all four search paths. Handle `providers=None` as "all providers" (no filter).

---

### Bug P3-PRE-4 — Embedding constants hardcoded, disconnected from `settings`

- **Root cause**: `config.py` defines `embedding_model`, `embedding_dimensions`, and `embedding_cache_ttl_seconds` as configurable settings. `embedding_service.py` defines `EMBEDDING_MODEL = "text-embedding-3-small"`, `EMBEDDING_DIMS = 1536`, and `REDIS_TTL = 3600` as module-level constants and never imports from `settings`. Any change to `.env` is silently ignored by the service.
- **Impact**: The Phase 0 configuration contract is broken by Phase 2. Operators cannot change the embedding model via environment without modifying source code.
- **Fix**: Replace the three hardcoded constants with `settings.embedding_model`, `settings.embedding_dimensions`, and `settings.embedding_cache_ttl_seconds`.

---

### Bug P3-PRE-5 — Redis URL defaults to non-empty string

- **Root cause**: `config.py` declares `redis_url: str = Field(default="redis://localhost:6379")`. The lifespan guard is `if settings.redis_url:`. Because the default is never empty, every deployment that does not set `REDIS_URL` in `.env` attempts a Redis connection, fails, logs a warning, and silently degrades. There is no clean "I don't want Redis" signal.
- **Impact**: Noisy startup warnings on every local development run; no way to intentionally disable Redis caching without setting an env var.
- **Fix**: Change default to `redis_url: str = Field(default="")`. The existing `if settings.redis_url:` guard then works as intended.

---

### Bug P3-PRE-6 — `constraint_segment_conversation_index` not verified in tests, and blocked by a conflicting plain index in the live database

- **Root cause (test gap)**: `test_connection.py::test_neo4j_constraints_exist` checked five uniqueness constraints but omitted `constraint_segment_conversation_index`, which was added in Phase 1 to fix Bug P1-5. Also missing: `index_message_conversation_index` in the composite index test.
- **Root cause (DB conflict)**: A plain composite index `index_segment_conversation_order` was created on `(Segment.conversationId, Segment.segmentIndex)` in a prior schema run. Neo4j refuses to create a uniqueness constraint on the same columns while that index exists. `setup_schema.py` silently skipped the constraint creation (the error was logged but the exit code was 0), leaving the database without the constraint. Full details in Bug P0-3 above.
- **Impact**: Without the constraint, the MERGE-based duplicate-segment prevention had no database-level enforcement. Tests gave a false green on a misconfigured DB.
- **Fix**:
  1. Added `constraint_segment_conversation_index` to `test_neo4j_constraints_exist` and `index_message_conversation_index` to `test_neo4j_composite_indexes_exist`.
  2. Removed the redundant `index_segment_conversation_order` from `SCHEMA_STATEMENTS` and added a pre-flight DROP in `setup_schema.py` (see Bug P0-3 for full details).

---

### Bug P3-PRE-7 — No end-to-end write→embed→query integration test

- **Root cause**: All Phase 2 tests seed Neo4j directly, bypassing the write pipeline entirely. There is no test that exercises the full cross-phase flow: `POST /memory/write` → BackgroundTask fires → embeddings land in Neo4j → `POST /memory/query` returns the written conversation.
- **Impact**: If `write_service.py` and `embedding_service.py` become misaligned (e.g., `userId` propagation, `tokenCount` conventions, `segmentId` naming), no test catches it.
- **Fix**: Add `TestEndToEnd` class in a new `tests/test_end_to_end.py` file. Write a conversation via the FastAPI TestClient (BackgroundTasks run synchronously), mock OpenAI to return a fixed embedding, then query with the same embedding and verify verbatim content is returned.

---

## Architecture Invariants (Never Violate)

1. **Zero hallucination** — the memory pipeline never calls a generative model at any stage. All stored content is verbatim input from the user.
2. **User-controlled storage** — writes happen only on explicit user trigger. The system never auto-captures conversations.
3. **Idempotent writes** — every write operation uses `MERGE` on a stable identifier (`messageId`, `conversationId`, `segmentIndex`). Re-sending the same data produces no duplicates and no state drift.
4. **Results consumed inside transactions** — all `tx.run()` result sets are fully consumed (`.data()` or `.single()`) before the transaction function returns. This is required by the Neo4j Python driver 6.x.
5. **Explicit driver lifecycle** — async drivers must be closed explicitly (`await driver.close()`). Do not rely on `__del__`; it no longer closes in Neo4j 6.x.
