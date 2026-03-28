"""
Phase 0 — Seed Sample Data
============================
Creates 5 sample Message nodes (across 2 conversations, 1 user)
to verify the graph structure, full-text search, and index behaviour
before Phase 1 development begins.

No embeddings are written here — that is Phase 2.
The nodes will be retrievable via full-text search only at this stage.

Usage:
    uv run python scripts/seed_sample_data.py

    # To wipe the seed data afterwards:
    uv run python scripts/seed_sample_data.py --clean
"""

import sys
import argparse
from pathlib import Path
from datetime import datetime, timezone

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from memory.db.connection import get_driver, close_driver, verify_connectivity
from memory.config import settings

console = Console()

# ── Sample data ───────────────────────────────────────────────────────────────
# Represents a realistic user who used ChatGPT and Claude on two different days.

SEED_USER = {
    "userId": "seed-user-001",
    "createdAt": datetime(2026, 3, 1, 10, 0, 0, tzinfo=timezone.utc),
    "lastActiveAt": datetime(2026, 3, 28, 9, 0, 0, tzinfo=timezone.utc),
}

SEED_CONVERSATIONS = [
    {
        "conversationId": "seed-conv-001",
        "userId": "seed-user-001",
        "provider": "chatgpt",
        "model": "gpt-4o",
        "startedAt": datetime(2026, 3, 5, 14, 30, 0, tzinfo=timezone.utc),
        "endedAt": datetime(2026, 3, 5, 15, 0, 0, tzinfo=timezone.utc),
        "messageCount": 2,
        "totalTokens": 1250,
        "segmentCount": 1,
        "isComplete": True,
    },
    {
        "conversationId": "seed-conv-002",
        "userId": "seed-user-001",
        "provider": "claude",
        "model": "claude-sonnet-4-6",
        "startedAt": datetime(2026, 3, 10, 9, 0, 0, tzinfo=timezone.utc),
        "endedAt": datetime(2026, 3, 10, 9, 30, 0, tzinfo=timezone.utc),
        "messageCount": 3,
        "totalTokens": 890,
        "segmentCount": 1,
        "isComplete": True,
    },
]

SEED_MESSAGES = [
    # ── Conversation 1: ChatGPT research session (March 5th) ──────────────────
    {
        "messageId": "seed-msg-001",
        "conversationId": "seed-conv-001",
        "userId": "seed-user-001",
        "role": "user",
        "content": "Give me detailed research about quantum computing error correction methods.",
        "provider": "chatgpt",
        "model": "gpt-4o",
        "timestamp": datetime(2026, 3, 5, 14, 30, 0, tzinfo=timezone.utc),
        "tokenCount": 14,
        "messageIndex": 0,
    },
    {
        "messageId": "seed-msg-002",
        "conversationId": "seed-conv-001",
        "userId": "seed-user-001",
        "role": "assistant",
        "content": (
            "# Quantum Computing Error Correction\n\n"
            "## Overview\n"
            "Quantum error correction (QEC) is essential for fault-tolerant quantum computing. "
            "Unlike classical bits, qubits are susceptible to decoherence and quantum noise.\n\n"
            "## Key Methods\n\n"
            "### 1. Shor Code\n"
            "The first quantum error-correcting code, encoding 1 logical qubit into 9 physical qubits. "
            "Protects against both bit-flip and phase-flip errors.\n\n"
            "### 2. Surface Codes\n"
            "Currently the most promising approach for near-term hardware. "
            "Arranges qubits in a 2D lattice. Error threshold ~1%, achievable with current hardware.\n\n"
            "### 3. Steane Code\n"
            "Encodes 1 logical qubit into 7 physical qubits. Based on classical Hamming codes.\n\n"
            "## Current State (2026)\n"
            "Google and IBM have demonstrated logical qubits with error rates below physical qubit rates "
            "using surface codes on 100+ qubit processors."
        ),
        "provider": "chatgpt",
        "model": "gpt-4o",
        "timestamp": datetime(2026, 3, 5, 14, 31, 0, tzinfo=timezone.utc),
        "tokenCount": 198,
        "messageIndex": 1,
    },

    # ── Conversation 2: Claude follow-up session (March 10th) ─────────────────
    {
        "messageId": "seed-msg-003",
        "conversationId": "seed-conv-002",
        "userId": "seed-user-001",
        "role": "user",
        "content": "I want to build a FastAPI backend for a Neo4j-powered memory system.",
        "provider": "claude",
        "model": "claude-sonnet-4-6",
        "timestamp": datetime(2026, 3, 10, 9, 0, 0, tzinfo=timezone.utc),
        "tokenCount": 17,
        "messageIndex": 0,
    },
    {
        "messageId": "seed-msg-004",
        "conversationId": "seed-conv-002",
        "userId": "seed-user-001",
        "role": "assistant",
        "content": (
            "For a FastAPI + Neo4j memory backend, here's a clean architecture:\n\n"
            "```python\n"
            "from fastapi import FastAPI\n"
            "from neo4j import AsyncGraphDatabase\n\n"
            "app = FastAPI()\n"
            "driver = AsyncGraphDatabase.driver('bolt://localhost:7687', auth=('neo4j', 'password'))\n\n"
            "@app.post('/memory/write')\n"
            "async def write_memory(payload: dict):\n"
            "    async with driver.session() as session:\n"
            "        await session.run('CREATE (m:Message $props)', props=payload)\n"
            "    return {'status': 'ok'}\n"
            "```\n\n"
            "Key considerations:\n"
            "- Use async driver for FastAPI compatibility\n"
            "- Connection pooling is handled automatically by the driver\n"
            "- Use MERGE instead of CREATE to avoid duplicates"
        ),
        "provider": "claude",
        "model": "claude-sonnet-4-6",
        "timestamp": datetime(2026, 3, 10, 9, 2, 0, tzinfo=timezone.utc),
        "tokenCount": 145,
        "messageIndex": 1,
    },
    {
        "messageId": "seed-msg-005",
        "conversationId": "seed-conv-002",
        "userId": "seed-user-001",
        "role": "user",
        "content": "Should I use MERGE or CREATE when writing message nodes to Neo4j?",
        "provider": "claude",
        "model": "claude-sonnet-4-6",
        "timestamp": datetime(2026, 3, 10, 9, 5, 0, tzinfo=timezone.utc),
        "tokenCount": 16,
        "messageIndex": 2,
    },
]


# ── Cypher queries ────────────────────────────────────────────────────────────

CREATE_USER = """
MERGE (u:User {userId: $userId})
SET u.createdAt    = $createdAt,
    u.lastActiveAt = $lastActiveAt
RETURN u.userId AS id
"""

CREATE_CONVERSATION = """
MERGE (c:Conversation {conversationId: $conversationId})
SET c.userId        = $userId,
    c.provider      = $provider,
    c.model         = $model,
    c.startedAt     = $startedAt,
    c.endedAt       = $endedAt,
    c.messageCount  = $messageCount,
    c.totalTokens   = $totalTokens,
    c.segmentCount  = $segmentCount,
    c.isComplete    = $isComplete
WITH c
MATCH (u:User {userId: $userId})
MERGE (u)-[:HAS_CONVERSATION]->(c)
RETURN c.conversationId AS id
"""

CREATE_MESSAGE = """
MERGE (m:Message {messageId: $messageId})
SET m.conversationId = $conversationId,
    m.userId         = $userId,
    m.role           = $role,
    m.content        = $content,
    m.provider       = $provider,
    m.model          = $model,
    m.timestamp      = $timestamp,
    m.tokenCount     = $tokenCount,
    m.messageIndex   = $messageIndex
WITH m
MATCH (c:Conversation {conversationId: $conversationId})
MERGE (c)-[:HAS_MESSAGE {order: $messageIndex}]->(m)
RETURN m.messageId AS id
"""

LINK_NEXT_MESSAGE = """
MATCH (a:Message {messageId: $fromId})
MATCH (b:Message {messageId: $toId})
MERGE (a)-[:NEXT_MESSAGE]->(b)
"""

DELETE_SEED_DATA = """
MATCH (u:User {userId: 'seed-user-001'})
OPTIONAL MATCH (u)-[:HAS_CONVERSATION]->(c:Conversation)
OPTIONAL MATCH (c)-[:HAS_MESSAGE]->(m:Message)
DETACH DELETE u, c, m
"""

VERIFY_FULLTEXT = """
CALL db.index.fulltext.queryNodes('index_fulltext_message', $searchText)
YIELD node, score
WHERE node.userId = 'seed-user-001'
RETURN node.messageId AS id, node.role AS role, score
ORDER BY score DESC
LIMIT 3
"""


def seed(driver) -> bool:
    with driver.session(database=settings.neo4j_database) as session:

        # User
        session.run(CREATE_USER, **SEED_USER)
        console.print("  [green]✓[/green] User node created")

        # Conversations
        for conv in SEED_CONVERSATIONS:
            session.run(CREATE_CONVERSATION, **conv)
            console.print(f"  [green]✓[/green] Conversation {conv['conversationId']} ({conv['provider']})")

        # Messages
        msgs_by_conv: dict[str, list] = {}
        for msg in SEED_MESSAGES:
            session.run(CREATE_MESSAGE, **msg)
            console.print(
                f"  [green]✓[/green] Message {msg['messageId']} "
                f"[dim]({msg['role']}, {msg['tokenCount']} tokens)[/dim]"
            )
            msgs_by_conv.setdefault(msg["conversationId"], []).append(msg)

        # NEXT_MESSAGE chains
        for conv_id, msgs in msgs_by_conv.items():
            sorted_msgs = sorted(msgs, key=lambda m: m["messageIndex"])
            for i in range(len(sorted_msgs) - 1):
                session.run(LINK_NEXT_MESSAGE,
                            fromId=sorted_msgs[i]["messageId"],
                            toId=sorted_msgs[i + 1]["messageId"])
            console.print(f"  [green]✓[/green] NEXT_MESSAGE chain linked for {conv_id}")

    return True


def verify_fulltext(driver) -> None:
    console.print("\n[bold]Testing full-text search on seed data...[/bold]")

    test_queries = [
        ("quantum computing", "Should match seed-msg-001 and seed-msg-002"),
        ("FastAPI Neo4j", "Should match seed-msg-003 and seed-msg-004"),
        ("MERGE CREATE", "Should match seed-msg-004 and seed-msg-005"),
    ]

    for query_text, expectation in test_queries:
        with driver.session(database=settings.neo4j_database) as session:
            result = session.run(VERIFY_FULLTEXT, searchText=query_text)
            rows = result.data()

        table = Table(title=f'Query: "{query_text}"', border_style="dim")
        table.add_column("messageId")
        table.add_column("role")
        table.add_column("score")

        for row in rows:
            table.add_row(row["id"], row["role"], f"{row['score']:.4f}")

        console.print(table)
        console.print(f"  [dim]{expectation}[/dim]")


def clean(driver) -> None:
    console.print("\n[bold yellow]Cleaning seed data...[/bold yellow]")
    with driver.session(database=settings.neo4j_database) as session:
        session.run(DELETE_SEED_DATA)
    console.print("[green]✓ Seed data removed[/green]")


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed or clean sample data for Phase 0 testing")
    parser.add_argument("--clean", action="store_true", help="Remove seed data instead of creating it")
    args = parser.parse_args()

    console.print(Panel.fit(
        "[bold cyan]Cross-LLM Memory — Phase 0: Sample Data[/bold cyan]",
        border_style="cyan",
    ))

    try:
        verify_connectivity()
        console.print("[green]✓ Connected to Neo4j[/green]")
    except Exception as exc:
        console.print(f"[red]✗ Cannot connect: {exc}[/red]")
        return 1

    driver = get_driver()

    if args.clean:
        clean(driver)
        return 0

    console.print("\n[bold]Creating seed data...[/bold]")
    try:
        seed(driver)
    except Exception as exc:
        console.print(f"[red]✗ Failed to seed data: {exc}[/red]")
        return 1

    verify_fulltext(driver)

    console.print(Panel.fit(
        "[bold green]5 sample messages seeded successfully.[/bold green]\n"
        "Open Neo4j Browser and run:\n"
        "[cyan]MATCH (u:User {userId: 'seed-user-001'})--(n) RETURN u, n[/cyan]\n"
        "to visualise the graph.\n\n"
        "To remove seed data:\n"
        "[cyan]uv run python scripts/seed_sample_data.py --clean[/cyan]",
        border_style="green",
    ))
    return 0


if __name__ == "__main__":
    try:
        exit_code = main()
    finally:
        close_driver()
    sys.exit(exit_code)
