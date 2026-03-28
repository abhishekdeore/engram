"""
Phase 0 — Neo4j Schema Setup
==============================
Creates all constraints and indexes required by the memory system.
Safe to re-run: every statement uses IF NOT EXISTS.

Usage:
    uv run python scripts/setup_schema.py
"""

import sys
import time
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import print as rprint

# Allow imports from src/
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from memory.db.connection import get_driver, close_driver, verify_connectivity
from memory.config import settings

console = Console()

# ── DDL Statements ───────────────────────────────────────────────────────────
#
# Ordered deliberately:
#   1. Uniqueness constraints  (implicitly create a backing index each)
#   2. Composite/range indexes (for fast user-scoped lookups)
#   3. Vector indexes          (for semantic similarity search)
#   4. Full-text index         (keyword fallback search)
#
# All use IF NOT EXISTS — fully idempotent.

SCHEMA_STATEMENTS = [

    # ── 1. Uniqueness Constraints ─────────────────────────────────────────────

    (
        "constraint_user_id",
        "Unique constraint: User.userId",
        """
        CREATE CONSTRAINT constraint_user_id IF NOT EXISTS
        FOR (u:User) REQUIRE u.userId IS UNIQUE
        """,
    ),
    (
        "constraint_conversation_id",
        "Unique constraint: Conversation.conversationId",
        """
        CREATE CONSTRAINT constraint_conversation_id IF NOT EXISTS
        FOR (c:Conversation) REQUIRE c.conversationId IS UNIQUE
        """,
    ),
    (
        "constraint_message_id",
        "Unique constraint: Message.messageId",
        """
        CREATE CONSTRAINT constraint_message_id IF NOT EXISTS
        FOR (m:Message) REQUIRE m.messageId IS UNIQUE
        """,
    ),
    (
        "constraint_segment_id",
        "Unique constraint: Segment.segmentId",
        """
        CREATE CONSTRAINT constraint_segment_id IF NOT EXISTS
        FOR (s:Segment) REQUIRE s.segmentId IS UNIQUE
        """,
    ),
    (
        "constraint_chunk_id",
        "Unique constraint: Chunk.chunkId",
        """
        CREATE CONSTRAINT constraint_chunk_id IF NOT EXISTS
        FOR (c:Chunk) REQUIRE c.chunkId IS UNIQUE
        """,
    ),
    (
        "constraint_segment_conversation_index",
        "Unique constraint: Segment (conversationId, segmentIndex) — prevents duplicate segments",
        """
        CREATE CONSTRAINT constraint_segment_conversation_index IF NOT EXISTS
        FOR (s:Segment) REQUIRE (s.conversationId, s.segmentIndex) IS UNIQUE
        """,
    ),

    # ── 2. Composite / Range Indexes ──────────────────────────────────────────
    #
    # These power:
    #   - Date pre-filter (userId + startedAt)
    #   - Per-user message listing (userId + timestamp)
    #   - Segment ordering within a conversation (conversationId + segmentIndex)

    (
        "index_conversation_user_date",
        "Composite index: Conversation (userId, startedAt) — date pre-filter",
        """
        CREATE INDEX index_conversation_user_date IF NOT EXISTS
        FOR (c:Conversation) ON (c.userId, c.startedAt)
        """,
    ),
    (
        "index_message_user_timestamp",
        "Composite index: Message (userId, timestamp) — per-user message queries",
        """
        CREATE INDEX index_message_user_timestamp IF NOT EXISTS
        FOR (m:Message) ON (m.userId, m.timestamp)
        """,
    ),
    (
        "index_segment_conversation_order",
        "Composite index: Segment (conversationId, segmentIndex) — segment ordering",
        """
        CREATE INDEX index_segment_conversation_order IF NOT EXISTS
        FOR (s:Segment) ON (s.conversationId, s.segmentIndex)
        """,
    ),
    (
        "index_message_conversation",
        "Index: Message.conversationId — fast conversation→messages lookup",
        """
        CREATE INDEX index_message_conversation IF NOT EXISTS
        FOR (m:Message) ON (m.conversationId)
        """,
    ),
    (
        "index_segment_conversation",
        "Index: Segment.conversationId — fast conversation→segments lookup",
        """
        CREATE INDEX index_segment_conversation IF NOT EXISTS
        FOR (s:Segment) ON (s.conversationId)
        """,
    ),
    (
        "index_chunk_message",
        "Index: Chunk.messageId — fast message→chunks lookup",
        """
        CREATE INDEX index_chunk_message IF NOT EXISTS
        FOR (c:Chunk) ON (c.messageId)
        """,
    ),
    (
        "index_message_conversation_index",
        "Composite index: Message (conversationId, messageIndex) — fast range queries for segmentation",
        """
        CREATE INDEX index_message_conversation_index IF NOT EXISTS
        FOR (m:Message) ON (m.conversationId, m.messageIndex)
        """,
    ),

    # ── 3. Vector Indexes ─────────────────────────────────────────────────────
    #
    # Dimensions: 1536 (text-embedding-3-small)
    # Similarity: cosine
    #
    # These will be EMPTY until Phase 2 writes embeddings.
    # The indexes exist now so Phase 2 doesn't need schema changes.

    (
        "index_vector_segment",
        "Vector index: Segment.embedding (1536 dims, cosine)",
        """
        CREATE VECTOR INDEX index_vector_segment IF NOT EXISTS
        FOR (s:Segment) ON (s.embedding)
        OPTIONS {
            indexConfig: {
                `vector.dimensions`: 1536,
                `vector.similarity_function`: 'cosine'
            }
        }
        """,
    ),
    (
        "index_vector_message",
        "Vector index: Message.embedding (1536 dims, cosine)",
        """
        CREATE VECTOR INDEX index_vector_message IF NOT EXISTS
        FOR (m:Message) ON (m.embedding)
        OPTIONS {
            indexConfig: {
                `vector.dimensions`: 1536,
                `vector.similarity_function`: 'cosine'
            }
        }
        """,
    ),
    (
        "index_vector_chunk",
        "Vector index: Chunk.embedding (1536 dims, cosine)",
        """
        CREATE VECTOR INDEX index_vector_chunk IF NOT EXISTS
        FOR (c:Chunk) ON (c.embedding)
        OPTIONS {
            indexConfig: {
                `vector.dimensions`: 1536,
                `vector.similarity_function`: 'cosine'
            }
        }
        """,
    ),

    # ── 4. Full-Text Index ────────────────────────────────────────────────────
    #
    # Keyword fallback: used when vector embeddings are not yet computed
    # or when no semantic match clears the similarity threshold.

    (
        "index_fulltext_message",
        "Full-text index: Message.content — keyword fallback search",
        """
        CREATE FULLTEXT INDEX index_fulltext_message IF NOT EXISTS
        FOR (m:Message) ON EACH [m.content]
        """,
    ),
]


def run_statement(driver, name: str, description: str, cypher: str) -> dict:
    start = time.perf_counter()
    try:
        with driver.session(database=settings.neo4j_database) as session:
            session.run(cypher.strip())
        elapsed = (time.perf_counter() - start) * 1000
        return {"name": name, "description": description, "status": "ok", "ms": elapsed}
    except Exception as exc:
        elapsed = (time.perf_counter() - start) * 1000
        return {
            "name": name,
            "description": description,
            "status": "error",
            "ms": elapsed,
            "error": str(exc),
        }


def main() -> int:
    console.print(Panel.fit(
        "[bold cyan]Cross-LLM Memory — Phase 0: Schema Setup[/bold cyan]\n"
        f"Target: [yellow]{settings.neo4j_uri}[/yellow] / db: [yellow]{settings.neo4j_database}[/yellow]",
        border_style="cyan",
    ))

    # ── Step 1: Verify connection ─────────────────────────────────────────────
    console.print("\n[bold]Verifying Neo4j connectivity...[/bold]")
    try:
        verify_connectivity()
        console.print("[green]✓ Connected to Neo4j[/green]")
    except Exception as exc:
        console.print(f"[red]✗ Cannot connect to Neo4j: {exc}[/red]")
        console.print(
            "\n[yellow]Checklist:[/yellow]\n"
            "  1. Is Neo4j Desktop running?\n"
            "  2. Is your database started (green play button)?\n"
            "  3. Check NEO4J_PASSWORD in your .env file\n"
            "  4. Default bolt port is 7687 — confirm in Neo4j Desktop settings"
        )
        return 1

    # ── Step 2: Run all DDL ───────────────────────────────────────────────────
    driver = get_driver()
    results = []

    console.print(f"\n[bold]Applying {len(SCHEMA_STATEMENTS)} schema statements...[/bold]\n")

    for name, description, cypher in SCHEMA_STATEMENTS:
        result = run_statement(driver, name, description, cypher)
        results.append(result)

        if result["status"] == "ok":
            console.print(f"  [green]✓[/green] {description} [dim]({result['ms']:.1f}ms)[/dim]")
        else:
            console.print(f"  [red]✗[/red] {description}")
            console.print(f"    [red]Error: {result['error']}[/red]")

    # ── Step 3: Summary ───────────────────────────────────────────────────────
    ok_count = sum(1 for r in results if r["status"] == "ok")
    err_count = sum(1 for r in results if r["status"] == "error")

    table = Table(title="\nSchema Setup Summary", border_style="cyan")
    table.add_column("Result", style="bold")
    table.add_column("Count")

    table.add_row("[green]Applied / already existed[/green]", str(ok_count))
    table.add_row("[red]Errors[/red]", str(err_count))
    table.add_row("Total statements", str(len(SCHEMA_STATEMENTS)))

    console.print(table)

    if err_count == 0:
        console.print(Panel.fit(
            "[bold green]Phase 0 schema setup complete.[/bold green]\n"
            "Run [cyan]uv run python scripts/verify_schema.py[/cyan] to confirm.",
            border_style="green",
        ))
        return 0
    else:
        console.print(Panel.fit(
            f"[bold red]{err_count} statement(s) failed.[/bold red]\n"
            "Fix the errors above and re-run. All statements are idempotent.",
            border_style="red",
        ))
        return 1


if __name__ == "__main__":
    try:
        exit_code = main()
    finally:
        close_driver()
    sys.exit(exit_code)
