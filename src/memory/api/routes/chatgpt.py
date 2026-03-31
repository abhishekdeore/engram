"""
ChatGPT Custom GPT Action routes — Phase 5
==========================================
POST /chatgpt/write        — store a ChatGPT conversation verbatim.
                             Accepts a simplified payload (role + content only).
                             Normalises internally via the chatgpt adapter.
                             Returns 202 immediately; storage is async.

GET  /chatgpt/action-spec  — serve the OpenAPI 3.1 action spec that users
                             upload to their Custom GPT Action configuration.
                             The server URL is injected from settings so the
                             spec always points at the live instance.

Design:
  - The write endpoint is the only new surface area; it calls the SAME
    write_conversation_to_graph service as POST /memory/write.
  - The chatgpt.py adapter (Phase 3) handles all normalization — no
    new normalization logic lives here.
  - Auth, rate limiting, and ownership enforcement are identical to
    POST /memory/write. No new security surface is introduced.
  - conversationId is optional: if omitted the server generates a UUID v4.
    Providing a stable conversationId enables idempotent writes.
"""

import logging
import uuid

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status
from fastapi.responses import JSONResponse

from ...adapters import chatgpt as chatgpt_adapter
from ...config import settings
from ...models.requests import (
    ChatGPTWriteRequest,
    ChatGPTWriteResponse,
    MessageIn,
    WriteRequest,
)
from ...services.write_service import write_conversation_to_graph
from ..dependencies import CurrentUserId, Neo4jDriver, OpenAIClient, RedisClient
from ..limiter import limiter

logger = logging.getLogger(__name__)

_WRITE_RATE = f"{settings.rate_limit_write_per_minute}/minute"

router = APIRouter(prefix="/chatgpt", tags=["chatgpt"])


# ── Write ─────────────────────────────────────────────────────────────────────

@router.post(
    "/write",
    response_model=ChatGPTWriteResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Store a ChatGPT conversation from a Custom GPT Action",
    description=(
        "Accepts a simplified payload from a ChatGPT Custom GPT Action — "
        "role + content messages only, no pre-generated IDs or timestamps needed. "
        "The server normalises to CMF using the chatgpt adapter and queues "
        "verbatim storage + embedding asynchronously. "
        "The userId must match the Bearer token sub claim."
    ),
)
@limiter.limit(_WRITE_RATE)
async def chatgpt_write(
    request: Request,
    body: ChatGPTWriteRequest,
    background_tasks: BackgroundTasks,
    current_user_id: CurrentUserId,
    driver: Neo4jDriver,
    openai_client: OpenAIClient,
    redis_client: RedisClient,
) -> ChatGPTWriteResponse:
    request.state.authenticated_user_id = current_user_id

    # Ownership check — same pattern as POST /memory/write
    if body.userId != current_user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Token userId '{current_user_id}' does not match "
                f"request userId '{body.userId}'."
            ),
        )

    # Resolve conversationId — generate if not provided
    conversation_id = body.conversationId or str(uuid.uuid4())

    # Normalise via the existing chatgpt adapter.
    # We construct a synthetic "completions API" object so _normalize_completions
    # handles messageId generation and timestamp normalisation — reusing all
    # the same logic that was tested in Phase 3.
    synthetic_raw = {
        "id":       conversation_id,
        "model":    body.model,
        "messages": [{"role": m.role, "content": m.content} for m in body.messages],
    }
    cmf_messages = chatgpt_adapter.normalize(synthetic_raw)

    if not cmf_messages:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "No storable messages after filtering. "
                "Ensure at least one user or assistant message has non-empty content."
            ),
        )

    # Build the standard WriteRequest — identical contract to POST /memory/write
    try:
        write_request = WriteRequest(
            userId=current_user_id,
            conversationId=conversation_id,
            provider="chatgpt",
            model=body.model,
            messages=[MessageIn(**m) for m in cmf_messages],
        )
    except Exception as exc:
        logger.error(
            "chatgpt_write_request_build_failed conversation_id=%s error=%s",
            conversation_id, exc,
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Failed to build write request: {exc}",
        ) from exc

    background_tasks.add_task(
        write_conversation_to_graph,
        driver,
        write_request,
        openai_client,
        redis_client,
    )

    logger.info(
        "chatgpt_write_queued conversation_id=%s messages=%d model=%s user=%s",
        conversation_id,
        len(cmf_messages),
        body.model,
        current_user_id,
    )

    return ChatGPTWriteResponse(
        conversationId=conversation_id,
        messageCount=len(cmf_messages),
    )


# ── Action spec ───────────────────────────────────────────────────────────────

@router.get(
    "/action-spec",
    summary="OpenAPI action spec for ChatGPT Custom GPT configuration",
    description=(
        "Returns the OpenAPI 3.1 spec that defines the memory_write and "
        "memory_query actions for a Custom GPT. Upload the response body "
        "(or the chatgpt_integration/action_spec.json file) to the Custom "
        "GPT's Action configuration in the ChatGPT editor."
    ),
    include_in_schema=False,   # not part of the Engram API — it IS the spec
)
async def get_action_spec(request: Request) -> JSONResponse:
    """
    Serve the OpenAPI action spec with the real server URL injected.
    The server URL is derived from the incoming request so it works
    correctly whether accessed via localhost or a public domain.
    """
    base_url = str(request.base_url).rstrip("/")

    spec = {
        "openapi": "3.1.0",
        "info": {
            "title": "Engram Memory — ChatGPT Action",
            "description": (
                "Persistent verbatim memory across LLMs. "
                "Write conversations to memory and query them back."
            ),
            "version": "1.0.0",
        },
        "servers": [{"url": base_url}],
        "paths": {
            "/chatgpt/write": {
                "post": {
                    "operationId": "memory_write",
                    "summary": "Save a conversation to persistent memory",
                    "description": (
                        "Store the current conversation verbatim. "
                        "Call this ONLY when the user explicitly asks to save, "
                        "store, remember, or archive the conversation."
                    ),
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": "#/components/schemas/ChatGPTWriteRequest"
                                }
                            }
                        },
                    },
                    "responses": {
                        "202": {
                            "description": "Conversation accepted for storage.",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/ChatGPTWriteResponse"
                                    }
                                }
                            },
                        },
                        "401": {"description": "Missing or invalid Bearer token."},
                        "403": {"description": "userId does not match token."},
                        "422": {"description": "No storable messages after filtering."},
                    },
                    "security": [{"BearerAuth": []}],
                }
            },
            "/memory/query": {
                "post": {
                    "operationId": "memory_query",
                    "summary": "Query persistent memory",
                    "description": (
                        "Retrieve verbatim conversation content semantically "
                        "relevant to the query. Call this when the user asks "
                        "about past conversations or when context from memory "
                        "would improve the response."
                    ),
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": "#/components/schemas/QueryRequest"
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "Verbatim memory results.",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/QueryResponse"
                                    }
                                }
                            },
                        },
                        "401": {"description": "Missing or invalid Bearer token."},
                    },
                    "security": [{"BearerAuth": []}],
                }
            },
        },
        "components": {
            "securitySchemes": {
                "BearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "description": (
                        "Engram API key. Generate one via POST /auth/apikey. "
                        "Configure as the API key in your Custom GPT Action settings."
                    ),
                }
            },
            "schemas": {
                "ChatGPTMessage": {
                    "type": "object",
                    "required": ["role", "content"],
                    "properties": {
                        "role": {
                            "type": "string",
                            "description": "Message role: user or assistant.",
                        },
                        "content": {
                            "type": "string",
                            "description": "Verbatim message content.",
                        },
                    },
                },
                "ChatGPTWriteRequest": {
                    "type": "object",
                    "required": ["userId", "messages"],
                    "properties": {
                        "userId": {
                            "type": "string",
                            "description": "Your Engram userId (must match the API key).",
                        },
                        "conversationId": {
                            "type": "string",
                            "description": (
                                "Stable UUID for this conversation. "
                                "Generate once per conversation for idempotent writes. "
                                "If omitted the server auto-generates one."
                            ),
                        },
                        "model": {
                            "type": "string",
                            "default": "gpt-4o",
                            "description": "OpenAI model name, e.g. gpt-4o.",
                        },
                        "messages": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/ChatGPTMessage"},
                            "description": "Conversation turns. system/tool roles are ignored.",
                        },
                        "title": {
                            "type": "string",
                            "description": "Optional conversation title.",
                        },
                    },
                },
                "ChatGPTWriteResponse": {
                    "type": "object",
                    "properties": {
                        "status": {"type": "string"},
                        "message": {"type": "string"},
                        "conversationId": {"type": "string"},
                        "messageCount": {"type": "integer"},
                        "provider": {"type": "string"},
                    },
                },
                "QueryRequest": {
                    "type": "object",
                    "required": ["userId", "query"],
                    "properties": {
                        "userId": {
                            "type": "string",
                            "description": "Your Engram userId.",
                        },
                        "query": {
                            "type": "string",
                            "description": "Natural language query to search memory.",
                        },
                        "topK": {
                            "type": "integer",
                            "default": 5,
                            "description": "Maximum number of results to return.",
                        },
                        "tokenBudget": {
                            "type": "integer",
                            "default": 4000,
                            "description": "Maximum tokens in the assembled response.",
                        },
                        "providers": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Filter by provider (chatgpt, claude, gemini, …). Omit for all.",
                        },
                    },
                },
                "QueryResponse": {
                    "type": "object",
                    "properties": {
                        "results": {
                            "type": "array",
                            "description": "Verbatim conversation results ranked by relevance.",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "conversationId": {"type": "string"},
                                    "provider": {"type": "string"},
                                    "model": {"type": "string"},
                                    "score": {"type": "number"},
                                    "messages": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "role": {"type": "string"},
                                                "content": {"type": "string"},
                                                "timestamp": {"type": "string"},
                                            },
                                        },
                                    },
                                },
                            },
                        },
                        "totalResults": {"type": "integer"},
                        "tokenCount": {"type": "integer"},
                        "queryLatencyMs": {"type": "number"},
                        "dateFilterApplied": {"type": "boolean"},
                        "searchMode": {"type": "string"},
                    },
                },
            },
        },
    }

    return JSONResponse(content=spec)
