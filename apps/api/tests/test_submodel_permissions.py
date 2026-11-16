"""The submodel permission split, pinned where it cannot drift.

The frontend hides what a user may not do, but the backend is the rule.
These tests walk the routes themselves and assert that:

* every ``/platform/submodels`` route depends on ``require_platform_admin``
  (a client admin or a member who guesses an id gets a 403);
* every client ``/submodels`` route depends on ``require_org_member`` (an
  anonymous caller gets a 401);
* no client route can create, train, publish or download a submodel — the
  client router only reads published submodels and records its own actions.
"""

from __future__ import annotations

from collections.abc import Iterator

from fastapi.routing import APIRoute

from app.api.deps import require_org_member, require_platform_admin
from app.main import app


def _routes() -> Iterator[APIRoute]:
    def walk(router) -> Iterator[APIRoute]:
        for route in getattr(router, "routes", []):
            if isinstance(route, APIRoute):
                yield route
                continue
            inner = getattr(route, "original_router", None)
            if inner is not None:
                yield from walk(inner)
            elif hasattr(route, "routes"):
                yield from walk(route)

    yield from walk(app.router)


def _depends_on(route: APIRoute, target) -> bool:
    seen: list[int] = []

    def visit(dependant) -> bool:
        if id(dependant) in seen:
            return False
        seen.append(id(dependant))
        if dependant.call is target:
            return True
        if any(visit(child) for child in dependant.dependencies):
            return True
        return False

    return visit(route.dependant)


def _admin_routes() -> list[APIRoute]:
    return [
        route
        for route in _routes()
        if route.path.startswith("/platform/submodels")
    ]


def _client_routes() -> list[APIRoute]:
    return [
        route
        for route in _routes()
        if route.path == "/submodels" or route.path.startswith("/submodels/")
    ]


def test_every_admin_route_requires_a_platform_admin() -> None:
    admin_routes = _admin_routes()
    assert len(admin_routes) >= 20, "the admin router should be complete"
    missing = [
        f"{sorted(route.methods)} {route.path}"
        for route in admin_routes
        if not _depends_on(route, require_platform_admin)
    ]
    assert not missing, f"routes without require_platform_admin: {missing}"


def test_every_client_route_requires_a_member() -> None:
    client_routes = _client_routes()
    assert len(client_routes) >= 6, "the client router should be complete"
    missing = [
        f"{sorted(route.methods)} {route.path}"
        for route in client_routes
        if not _depends_on(route, require_org_member)
    ]
    assert not missing, f"routes without require_org_member: {missing}"


def test_the_client_router_cannot_manage_submodels() -> None:
    """A client may only use a published submodel, never administer one.

    The mutating verbs on the client router exist for exactly two things:
    asking for guidance and recording the action the user confirmed. No
    create, train, publish, upload or download route is reachable there.
    """
    forbidden_fragments = (
        "/train",
        "/publish",
        "/unpublish",
        "/materials",
        "/workflows",
        "/package",
        "/download",
        "/import",
    )
    violations = [
        f"{sorted(route.methods)} {route.path}"
        for route in _client_routes()
        if any(fragment in route.path for fragment in forbidden_fragments)
    ]
    assert not violations, f"admin functionality on the client router: {violations}"

    writes = [
        f"{sorted(route.methods)} {route.path}"
        for route in _client_routes()
        if route.methods - {"GET", "HEAD", "OPTIONS", "POST"}
    ]
    assert not writes, f"unexpected verbs on the client router: {writes}"


def test_the_client_router_serves_no_admin_paths() -> None:
    """The two routers never overlap: ``/platform/submodels`` is admin only."""
    for route in _client_routes():
        assert not route.path.startswith("/platform/"), route.path
    for route in _admin_routes():
        assert route.path.startswith("/platform/submodels"), route.path
