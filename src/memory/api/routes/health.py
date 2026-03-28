from fastapi import APIRouter, Request

from ...models.requests import HealthResponse
from ... import __version__

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Health check",
    description="Returns service status and Neo4j connectivity.",
)
async def health(request: Request) -> HealthResponse:
    driver = getattr(request.app.state, "neo4j_driver", None)

    if driver is None:
        return HealthResponse(status="down", neo4j="disconnected", version=__version__)

    try:
        await driver.verify_connectivity()
        neo4j_status = "connected"
        svc_status   = "ok"
    except Exception:
        neo4j_status = "disconnected"
        svc_status   = "degraded"

    return HealthResponse(status=svc_status, neo4j=neo4j_status, version=__version__)
