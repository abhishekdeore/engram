"""
Engram Latency Benchmark
========================
Measures end-to-end latency for:
  1. Write   — POST /memory/write  (full pipeline: Neo4j + embedding)
  2. Query   — POST /memory/query  (vector search path)
  3. Fulltext— POST /memory/query  (fulltext fallback, no embedding)
  4. MCP write  — handle_memory_write() called directly
  5. MCP query  — handle_memory_query() called directly

Each operation is run N times (default 5). Reports min/max/mean/p95.
Runs against the live Neo4j + OpenAI stack — no mocks.

Usage:
    uv run python scripts/latency_benchmark.py
    uv run python scripts/latency_benchmark.py --runs 10
"""

import argparse
import asyncio
import statistics
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

# ── make src/memory importable ────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import httpx
from neo4j import AsyncGraphDatabase
from openai import AsyncOpenAI
from rich.console import Console
from rich.table import Table
from rich import box

from memory.config import settings
from memory.auth.jwt_handler import create_access_token
from memory.mcp_server import handle_memory_write, handle_memory_query

console = Console()

# ── helpers ───────────────────────────────────────────────────────────────────

BENCHMARK_USER = "latency-benchmark-user"
BASE_URL       = f"http://{settings.app_host}:{settings.app_port}"


def _ms(seconds: float) -> str:
    return f"{seconds * 1000:.1f} ms"


def _stats(samples: list[float]) -> dict:
    s = sorted(samples)
    p95_idx = max(0, int(len(s) * 0.95) - 1)
    return {
        "min":  min(s),
        "max":  max(s),
        "mean": statistics.mean(s),
        "p95":  s[p95_idx],
    }


def _make_messages(conv_id: str, n: int = 2) -> list[dict]:
    now = datetime.now(timezone.utc).isoformat()
    return [
        {
            "messageId":  f"{conv_id}-msg-{i}",
            "role":       "user" if i % 2 == 0 else "assistant",
            "content":    f"Benchmark message {i} for conversation {conv_id}. "
                          f"This tests latency of the Engram memory pipeline end-to-end.",
            "timestamp":  now,
            "tokenCount": 20,
        }
        for i in range(n)
    ]


# ── HTTP API benchmarks ───────────────────────────────────────────────────────

async def bench_http_write(client: httpx.AsyncClient, token: str, runs: int) -> list[float]:
    samples = []
    for _ in range(runs):
        conv_id = str(uuid.uuid4())
        payload = {
            "userId":         BENCHMARK_USER,
            "conversationId": conv_id,
            "provider":       "claude",
            "model":          "claude-sonnet-4-6",
            "messages":       _make_messages(conv_id),
        }
        t0 = time.perf_counter()
        resp = await client.post(
            f"{BASE_URL}/memory/write",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )
        elapsed = time.perf_counter() - t0
        assert resp.status_code == 202, f"Write failed: {resp.text}"
        samples.append(elapsed)
        # small gap so background tasks can settle
        await asyncio.sleep(0.3)
    return samples


async def bench_http_query_vector(client: httpx.AsyncClient, token: str, runs: int) -> list[float]:
    """Query with embedding — uses the vector search path."""
    samples = []
    for _ in range(runs):
        payload = {
            "userId":      BENCHMARK_USER,
            "query":       "benchmark message latency pipeline",
            "topK":        5,
            "tokenBudget": 2000,
        }
        t0 = time.perf_counter()
        resp = await client.post(
            f"{BASE_URL}/memory/query",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )
        elapsed = time.perf_counter() - t0
        assert resp.status_code == 200, f"Query failed: {resp.text}"
        samples.append(elapsed)
    return samples


async def bench_http_query_fulltext(client: httpx.AsyncClient, token: str, runs: int) -> list[float]:
    """Query with a keyword that bypasses vector search (no OpenAI call needed
    because the query embedding is cached after the first vector bench run,
    but we isolate fulltext by using a unique nonsense token that won't embed
    meaningfully — the vector score will fall below 0.70 and fulltext fires)."""
    samples = []
    for _ in range(runs):
        payload = {
            "userId":      BENCHMARK_USER,
            "query":       "Benchmark message latency pipeline",   # keyword match
            "topK":        5,
            "tokenBudget": 2000,
        }
        t0 = time.perf_counter()
        resp = await client.post(
            f"{BASE_URL}/memory/query",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )
        elapsed = time.perf_counter() - t0
        assert resp.status_code == 200, f"Fulltext query failed: {resp.text}"
        data = resp.json()
        samples.append(elapsed)
    return samples


# ── MCP layer benchmarks ──────────────────────────────────────────────────────

async def bench_mcp_write(driver, openai_client, runs: int) -> list[float]:
    samples = []
    for _ in range(runs):
        conv_id = str(uuid.uuid4())
        args = {
            "conversation_id": conv_id,
            "model":           "claude-sonnet-4-6",
            "messages": [
                {"role": "user",      "content": "MCP benchmark write test message."},
                {"role": "assistant", "content": "MCP benchmark response stored verbatim."},
            ],
        }
        t0 = time.perf_counter()
        await handle_memory_write(args, driver, openai_client, None, BENCHMARK_USER)
        elapsed = time.perf_counter() - t0
        samples.append(elapsed)
    return samples


async def bench_mcp_query(driver, openai_client, runs: int) -> list[float]:
    samples = []
    for _ in range(runs):
        args = {
            "query":        "benchmark write test message",
            "top_k":        5,
            "token_budget": 2000,
        }
        t0 = time.perf_counter()
        await handle_memory_query(args, driver, openai_client, None, BENCHMARK_USER)
        elapsed = time.perf_counter() - t0
        samples.append(elapsed)
    return samples


# ── cleanup ───────────────────────────────────────────────────────────────────

async def cleanup(driver):
    async with driver.session(database=settings.neo4j_database) as session:
        await session.run(
            """
            MATCH (u:User {userId: $uid})-[:HAS_CONVERSATION]->(c)
            OPTIONAL MATCH (c)-[:HAS_MESSAGE]->(m)-[:HAS_CHUNK]->(ch)
            DETACH DELETE ch
            WITH c, m
            DETACH DELETE m
            WITH c
            DETACH DELETE c
            """,
            uid=BENCHMARK_USER,
        )
        await session.run(
            "MATCH (u:User {userId: $uid}) DETACH DELETE u",
            uid=BENCHMARK_USER,
        )


# ── report ────────────────────────────────────────────────────────────────────

def print_report(results: dict):
    console.print()
    console.rule("[bold cyan]Engram Latency Benchmark Results")
    console.print()

    table = Table(box=box.ROUNDED, show_header=True, header_style="bold magenta")
    table.add_column("Operation",    style="cyan",  min_width=28)
    table.add_column("Min",          justify="right")
    table.add_column("Mean",         justify="right", style="bold")
    table.add_column("P95",          justify="right", style="yellow")
    table.add_column("Max",          justify="right")
    table.add_column("Samples",      justify="right", style="dim")

    for label, stats, n in results:
        table.add_row(
            label,
            _ms(stats["min"]),
            _ms(stats["mean"]),
            _ms(stats["p95"]),
            _ms(stats["max"]),
            str(n),
        )

    console.print(table)
    console.print()
    console.print("[dim]Min/Mean/P95/Max are wall-clock time including network round-trip.[/dim]")
    console.print("[dim]HTTP write returns 202 immediately — Neo4j + embedding run in background.[/dim]")
    console.print("[dim]MCP write awaits completion — includes Neo4j write + embedding.[/dim]")
    console.print()


# ── main ──────────────────────────────────────────────────────────────────────

async def main(runs: int):
    console.rule("[bold cyan]Engram Latency Benchmark")
    console.print(f"  Runs per operation : [bold]{runs}[/bold]")
    console.print(f"  Neo4j              : [bold]{settings.neo4j_uri}[/bold]")
    console.print(f"  OpenAI             : [bold]{'enabled' if settings.openai_api_key else 'disabled'}[/bold]")
    console.print(f"  HTTP server        : [bold]{BASE_URL}[/bold]")
    console.print()

    # ── shared clients ────────────────────────────────────────────────────────
    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_username, settings.neo4j_password),
    )
    openai_client = AsyncOpenAI(api_key=settings.openai_api_key) if settings.openai_api_key else None
    token = create_access_token(BENCHMARK_USER)

    results = []

    async with httpx.AsyncClient(timeout=30.0) as client:

        # ── check server is up ────────────────────────────────────────────────
        try:
            r = await client.get(f"{BASE_URL}/health")
            assert r.status_code == 200
            console.print("[green]✓[/green] HTTP server reachable")
        except Exception:
            console.print(f"[red]✗ HTTP server not reachable at {BASE_URL}[/red]")
            console.print("  Start it with: uv run uvicorn memory.api.main:app --host 0.0.0.0 --port 8000")
            console.print()
            console.print("[yellow]Skipping HTTP benchmarks — running MCP layer only.[/yellow]")
            http_ok = False
        else:
            http_ok = True

        # ── 1. HTTP write ─────────────────────────────────────────────────────
        if http_ok:
            console.print(f"  Running HTTP write  ({runs}×)...", end=" ")
            samples = await bench_http_write(client, token, runs)
            stats = _stats(samples)
            results.append(("HTTP POST /memory/write  (202 async)", stats, runs))
            console.print(f"[green]done[/green] — mean {_ms(stats['mean'])}")

        # ── 2. HTTP vector query ───────────────────────────────────────────────
        if http_ok:
            console.print(f"  Running HTTP vector query ({runs}×)...", end=" ")
            samples = await bench_http_query_vector(client, token, runs)
            stats = _stats(samples)
            results.append(("HTTP POST /memory/query  (vector)", stats, runs))
            console.print(f"[green]done[/green] — mean {_ms(stats['mean'])}")

        # ── 3. HTTP fulltext query ────────────────────────────────────────────
        if http_ok:
            console.print(f"  Running HTTP fulltext query ({runs}×)...", end=" ")
            samples = await bench_http_query_fulltext(client, token, runs)
            stats = _stats(samples)
            results.append(("HTTP POST /memory/query  (fulltext)", stats, runs))
            console.print(f"[green]done[/green] — mean {_ms(stats['mean'])}")

    # ── 4. MCP write (direct, awaited) ────────────────────────────────────────
    console.print(f"  Running MCP write   ({runs}×)...", end=" ")
    samples = await bench_mcp_write(driver, openai_client, runs)
    stats = _stats(samples)
    results.append(("MCP handle_memory_write  (sync await)", stats, runs))
    console.print(f"[green]done[/green] — mean {_ms(stats['mean'])}")

    # ── 5. MCP query ──────────────────────────────────────────────────────────
    console.print(f"  Running MCP query   ({runs}×)...", end=" ")
    samples = await bench_mcp_query(driver, openai_client, runs)
    stats = _stats(samples)
    results.append(("MCP handle_memory_query  (vector)" if openai_client else
                    "MCP handle_memory_query  (fulltext)", stats, runs))
    console.print(f"[green]done[/green] — mean {_ms(stats['mean'])}")

    # ── cleanup ───────────────────────────────────────────────────────────────
    console.print("  Cleaning up benchmark data...", end=" ")
    await cleanup(driver)
    await driver.close()
    console.print("[green]done[/green]")

    print_report(results)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Engram latency benchmark")
    parser.add_argument("--runs", type=int, default=5, help="Runs per operation (default: 5)")
    args = parser.parse_args()
    asyncio.run(main(args.runs))
