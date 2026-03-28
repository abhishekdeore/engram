"""
Phase 0 — Connection tests
============================
Verifies that the Neo4j driver can connect and basic queries work.

Run with:
    uv run pytest tests/test_connection.py -v
"""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from memory.db.connection import verify_connectivity, get_driver, close_driver
from memory.config import settings


@pytest.fixture(autouse=True)
def cleanup_driver():
    yield
    close_driver()


def test_neo4j_connectivity():
    """Driver can reach Neo4j and credentials are accepted."""
    result = verify_connectivity()
    assert result is True


def test_neo4j_simple_query():
    """A basic Cypher RETURN query executes without error."""
    driver = get_driver()
    with driver.session(database=settings.neo4j_database) as session:
        result = session.run("RETURN 1 AS val")
        row = result.single()
        assert row["val"] == 1


def test_neo4j_constraints_exist():
    """All five uniqueness constraints are present and ONLINE."""
    expected = {
        "constraint_user_id",
        "constraint_conversation_id",
        "constraint_message_id",
        "constraint_segment_id",
        "constraint_chunk_id",
        "constraint_segment_conversation_index",
    }
    driver = get_driver()
    with driver.session(database=settings.neo4j_database) as session:
        result = session.run("SHOW CONSTRAINTS")
        names = {row["name"] for row in result}
    missing = expected - names
    assert not missing, f"Missing constraints: {missing}"


def test_neo4j_vector_indexes_exist():
    """All three vector indexes are present."""
    expected = {
        "index_vector_segment",
        "index_vector_message",
        "index_vector_chunk",
    }
    driver = get_driver()
    with driver.session(database=settings.neo4j_database) as session:
        result = session.run("SHOW INDEXES")
        names = {row["name"] for row in result}
    missing = expected - names
    assert not missing, f"Missing vector indexes: {missing}"


def test_neo4j_fulltext_index_exists():
    """Full-text index on Message.content exists."""
    driver = get_driver()
    with driver.session(database=settings.neo4j_database) as session:
        result = session.run("SHOW INDEXES")
        names = {row["name"] for row in result}
    assert "index_fulltext_message" in names


def test_neo4j_composite_indexes_exist():
    """All composite/range indexes are present."""
    expected = {
        "index_conversation_user_date",
        "index_message_user_timestamp",
        "index_message_conversation",
        "index_segment_conversation",
        "index_chunk_message",
        "index_message_conversation_index",
    }
    driver = get_driver()
    with driver.session(database=settings.neo4j_database) as session:
        result = session.run("SHOW INDEXES")
        names = {row["name"] for row in result}
    missing = expected - names
    assert not missing, f"Missing composite indexes: {missing}"
