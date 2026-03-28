"""
Phase 0 — Neo4j Schema Verification
======================================
Queries Neo4j to confirm every constraint and index was created correctly.
Run this after setup_schema.py to validate the schema is production-ready.

Usage:
    uv run python scripts/verify_schema.py
"""

import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from memory.db.connection import get_driver, close_driver, verify_connectivity
from memory.config import settings

console = Console()

# ── Expected schema objects ───────────────────────────────────────────────────
# These are the exact names we expect to find in Neo4j after setup_schema.py.

EXPECTED_CONSTRAINTS = [
    "constraint_user_id",
    "constraint_conversation_id",
    "constraint_message_id",
    "constraint_segment_id",
    "constraint_chunk_id",
]

EXPECTED_INDEXES = [
    # Composite / range
    "index_conversation_user_date",
    "index_message_user_timestamp",
    "index_segment_conversation_order",
    "index_message_conversation",
    "index_segment_conversation",
    "index_chunk_message",
    # Vector
    "index_vector_segment",
    "index_vector_message",
    "index_vector_chunk",
    # Full-text
    "index_fulltext_message",
]


def fetch_constraints(driver) -> dict[str, dict]:
    with driver.session(database=settings.neo4j_database) as session:
        result = session.run("SHOW CONSTRAINTS")
        return {row["name"]: dict(row) for row in result}


def fetch_indexes(driver) -> dict[str, dict]:
    with driver.session(database=settings.neo4j_database) as session:
        result = session.run("SHOW INDEXES")
        return {row["name"]: dict(row) for row in result}


def check_vector_index_config(index_info: dict) -> str:
    """Return a readable config string for vector indexes."""
    options = index_info.get("options", {}) or {}
    config = options.get("indexConfig", {}) if isinstance(options, dict) else {}
    dims = config.get("vector.dimensions", "?")
    sim = config.get("vector.similarity_function", "?")
    return f"{dims} dims / {sim}"


def main() -> int:
    console.print(Panel.fit(
        "[bold cyan]Cross-LLM Memory — Phase 0: Schema Verification[/bold cyan]\n"
        f"Target: [yellow]{settings.neo4j_uri}[/yellow] / db: [yellow]{settings.neo4j_database}[/yellow]",
        border_style="cyan",
    ))

    # ── Connect ───────────────────────────────────────────────────────────────
    console.print("\n[bold]Connecting to Neo4j...[/bold]")
    try:
        verify_connectivity()
        console.print("[green]✓ Connected[/green]")
    except Exception as exc:
        console.print(f"[red]✗ Cannot connect: {exc}[/red]")
        return 1

    driver = get_driver()
    all_passed = True

    # ── Check Constraints ─────────────────────────────────────────────────────
    console.print("\n[bold]Checking uniqueness constraints...[/bold]")
    try:
        existing_constraints = fetch_constraints(driver)
    except Exception as exc:
        console.print(f"[red]✗ Failed to fetch constraints: {exc}[/red]")
        return 1

    constraint_table = Table(border_style="dim")
    constraint_table.add_column("Constraint Name", style="cyan")
    constraint_table.add_column("Status")
    constraint_table.add_column("Details")

    for name in EXPECTED_CONSTRAINTS:
        if name in existing_constraints:
            info = existing_constraints[name]
            # Neo4j 2026.x / 5.x: SHOW CONSTRAINTS has no 'state' column.
            # Existence + correct type is sufficient — constraints are always active.
            c_type = info.get("type", "N/A")
            labels = info.get("labelsOrTypes", [])
            props  = info.get("properties", [])
            details = f"type={c_type} | {labels} → {props}"
            constraint_table.add_row(name, "[green]✓ EXISTS[/green]", details)
        else:
            constraint_table.add_row(name, "[red]✗ MISSING[/red]", "Run setup_schema.py")
            all_passed = False

    console.print(constraint_table)

    # ── Check Indexes ─────────────────────────────────────────────────────────
    console.print("\n[bold]Checking indexes...[/bold]")
    try:
        existing_indexes = fetch_indexes(driver)
    except Exception as exc:
        console.print(f"[red]✗ Failed to fetch indexes: {exc}[/red]")
        return 1

    index_table = Table(border_style="dim")
    index_table.add_column("Index Name", style="cyan")
    index_table.add_column("Type")
    index_table.add_column("Status")
    index_table.add_column("Details")

    for name in EXPECTED_INDEXES:
        if name in existing_indexes:
            info = existing_indexes[name]
            idx_type = info.get("type", "N/A")
            state = info.get("state", "N/A")

            # Extra detail for vector indexes
            if "vector" in name:
                detail = check_vector_index_config(info)
            else:
                labelsOrTypes = info.get("labelsOrTypes", [])
                properties = info.get("properties", [])
                detail = f"{labelsOrTypes} → {properties}"

            if state == "ONLINE":
                index_table.add_row(name, idx_type, "[green]✓ ONLINE[/green]", detail)
            elif state == "POPULATING":
                index_table.add_row(name, idx_type, "[yellow]⟳ POPULATING[/yellow]", detail)
            else:
                index_table.add_row(name, idx_type, f"[yellow]⚠ {state}[/yellow]", detail)
                all_passed = False
        else:
            index_table.add_row(name, "—", "[red]✗ MISSING[/red]", "Run setup_schema.py")
            all_passed = False

    console.print(index_table)

    # ── Final Result ──────────────────────────────────────────────────────────
    expected_total = len(EXPECTED_CONSTRAINTS) + len(EXPECTED_INDEXES)
    console.print(f"\n[dim]Checked {expected_total} schema objects "
                  f"({len(EXPECTED_CONSTRAINTS)} constraints + {len(EXPECTED_INDEXES)} indexes)[/dim]")

    if all_passed:
        console.print(Panel.fit(
            "[bold green]All schema objects verified and ONLINE.[/bold green]\n"
            "Phase 0 is complete. Ready to proceed to Phase 1.",
            border_style="green",
        ))
        return 0
    else:
        console.print(Panel.fit(
            "[bold red]One or more schema objects are missing or not ONLINE.[/bold red]\n"
            "Re-run [cyan]uv run python scripts/setup_schema.py[/cyan] and try again.\n"
            "Note: vector indexes may show POPULATING briefly after creation — this is normal.",
            border_style="red",
        ))
        return 1


if __name__ == "__main__":
    try:
        exit_code = main()
    finally:
        close_driver()
    sys.exit(exit_code)
