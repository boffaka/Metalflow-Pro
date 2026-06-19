"""
Endpoint deprecation shim — Lot B (API surface consolidation).

Marks a legacy endpoint as deprecated WITHOUT breaking it: emits a structured
log line on every hit (so production traffic over an observation window proves
whether the endpoint is truly dead) and returns RFC 8288 / draft-deprecation
response headers pointing clients at the successor endpoint.

`build_deprecation_headers` is pure (no FastAPI/DB) so it is unit-testable in
isolation; `deprecated_endpoint` is the FastAPI dependency factory that wires it
in and logs the access.
"""

import logging

logger = logging.getLogger("mpdpms.legacy_usage")


def build_deprecation_headers(successor: str | None = None) -> dict[str, str]:
    """Response headers signalling deprecation, optionally pointing at a successor.

    Args:
        successor: Path of the replacement endpoint, or None if none exists yet.

    Returns:
        Header dict — always ``Deprecation: true``; adds an RFC 8288
        ``Link: <successor>; rel="successor-version"`` when a successor is given.
    """
    headers: dict[str, str] = {"Deprecation": "true"}
    if successor:
        headers["Link"] = f'<{successor}>; rel="successor-version"'
    return headers


def deprecated_endpoint(successor: str | None = None):
    """FastAPI dependency: log the legacy hit and attach deprecation headers.

    Usage:
        @router.get("/old")
        def handler(response: Response, _=Depends(deprecated_endpoint("/new"))):
            ...
    The dependency never raises — a logging/header failure must not break the
    endpoint it guards.
    """
    from fastapi import Request, Response

    def dependency(request: Request, response: Response) -> None:
        try:
            logger.warning(
                "legacy_endpoint_hit path=%s method=%s successor=%s",
                request.url.path,
                request.method,
                successor or "-",
            )
            response.headers.update(build_deprecation_headers(successor))
        except Exception:  # pragma: no cover - never break the guarded endpoint
            pass

    return dependency
