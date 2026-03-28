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
Applies 17 DDL statements to Neo4j, all using `IF NOT EXISTS` (fully idempotent):

| # | Name | Type | Purpose |
|---|------|------|---------|
| 1 | `constraint_user_id` | Unique constraint | `User.userId` |
| 2 | `constraint_conversation_id` | Unique constraint | `Conversation.conversationId` |
| 3 | `constraint_message_id` | Unique constraint | `Message.messageId` |
| 4 | `constraint_segment_id` | Unique constraint | `Segment.segmentId` |
| 5 | `constraint_chunk_id` | Unique constraint | `Chunk.chunkId` |
| 6 | `constraint_segment_conversation_index` | Composite unique constraint | `(Segment.conversationId, Segment.segmentIndex)` — prevents duplicate segments *(added during Phase 1 review)* |
| 7 | `index_conversation_user_date` | Composite index | `(Conversation.userId, Conversation.startedAt)` — date pre-filter |
| 8 | `index_message_user_timestamp` | Composite index | `(Message.userId, Message.timestamp)` |
| 9 | `index_segment_conversation_order` | Composite index | `(Segment.conversationId, Segment.segmentIndex)` |
| 10 | `index_message_conversation` | Index | `Message.conversationId` |
| 11 | `index_segment_conversation` | Index | `Segment.conversationId` |
| 12 | `index_chunk_message` | Index | `Chunk.messageId` |
| 13 | `index_message_conversation_index` | Composite index | `(Message.conversationId, Message.messageIndex)` — segment range queries *(added during Phase 1 review)* |
| 14 | `index_vector_segment` | Vector index | `Segment.embedding` (1536 dims, cosine) — Phase 2 |
| 15 | `index_vector_message` | Vector index | `Message.embedding` (1536 dims, cosine) — Phase 2 |
| 16 | `index_vector_chunk` | Vector index | `Chunk.embedding` (1536 dims, cosine) — Phase 2 |
| 17 | `index_fulltext_message` | Full-text index | `Message.content` — keyword fallback search |

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

## Planned — Phase 2 (Not Yet Implemented)

| Feature | Description |
|---------|-------------|
| Embedding pipeline | Generate `text-embedding-3-small` vectors for each `Segment` via OpenAI; cache in Redis to avoid re-embedding |
| Vector search | `GET /memory/search` — semantic retrieval using `db.index.vector.queryNodes` on `index_vector_segment` |
| Date pre-filter | LLM resolves relative dates to `YYYY-MM-DD`; query filters `(userId, startedAt)` before vector search |
| Ranked retrieval | With date filter: semantic 80% + date match 10% + context 10%; without: semantic 70% + recency 20% + context 10% |
| Read endpoint | `GET /memory/read/:conversationId` — return full verbatim conversation |
| Chunk model | Sub-segment `Chunk` nodes for fine-grained retrieval on very long segments |

---

## Architecture Invariants (Never Violate)

1. **Zero hallucination** — the memory pipeline never calls a generative model at any stage. All stored content is verbatim input from the user.
2. **User-controlled storage** — writes happen only on explicit user trigger. The system never auto-captures conversations.
3. **Idempotent writes** — every write operation uses `MERGE` on a stable identifier (`messageId`, `conversationId`, `segmentIndex`). Re-sending the same data produces no duplicates and no state drift.
4. **Results consumed inside transactions** — all `tx.run()` result sets are fully consumed (`.data()` or `.single()`) before the transaction function returns. This is required by the Neo4j Python driver 6.x.
5. **Explicit driver lifecycle** — async drivers must be closed explicitly (`await driver.close()`). Do not rely on `__del__`; it no longer closes in Neo4j 6.x.
