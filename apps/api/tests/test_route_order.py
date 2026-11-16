"""Route registration order and the removals that must stay removed.

FastAPI matches routes in registration order, so a literal word in a path
must be registered before a path parameter that would swallow it. These
tests pin that order for the submodel routers, and keep the tests for the
subsystems that were deleted and must not come back.
"""

from __future__ import annotations

from collections.abc import Iterator

from fastapi.routing import APIRoute

from app.main import app


def _routes() -> list[APIRoute]:
    """Every route of the application, in registration order.

    The routers are included one in another, so the routes are collected by
    walking down into each included router.
    """

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

    return list(walk(app.router))


def _paths() -> list[str]:
    return [route.path for route in _routes()]


def _segments(path: str) -> list[str]:
    return [segment for segment in path.split("/") if segment]


def _first_match(method: str, url: str) -> APIRoute:
    """The first route that would answer this method and URL."""
    url_segments = _segments(url)
    for route in _routes():
        if method not in route.methods:
            continue
        route_segments = _segments(route.path)
        if len(route_segments) != len(url_segments):
            continue
        if all(
            expected.startswith("{") or expected == actual
            for expected, actual in zip(
                route_segments, url_segments, strict=False
            )
        ):
            return route
    raise AssertionError(f"No route matches {method} {url}")


def test_the_trained_extension_feature_is_gone() -> None:
    """The extension subsystem was removed; nothing serves it any more."""
    paths = _paths()
    assert not [path for path in paths if "/extensions" in path]


def test_capabilities_is_a_literal_route() -> None:
    """``capabilities`` must not be read as a submodel id.

    FastAPI tries routes in order, so the literal word has to be registered
    before ``/{submodel_id}`` — otherwise an administrator asking what the
    deployment can do gets a 422 for a malformed id.
    """
    route = _first_match("GET", "/platform/submodels/capabilities")
    assert route.path == "/platform/submodels/capabilities"


def test_import_is_a_literal_route() -> None:
    """``import`` must not be read as a submodel id either."""
    route = _first_match("POST", "/platform/submodels/import")
    assert route.path == "/platform/submodels/import"


def test_client_catalog_literal_comes_before_the_id_route() -> None:
    """``GET /submodels`` is the catalog, not a submodel with id "submodels"."""
    route = _first_match("GET", "/submodels")
    assert route.path == "/submodels"


def test_the_submodel_feature_is_served() -> None:
    """The submodel platform is back: both routers answer."""
    paths = _paths()
    admin = [p for p in paths if p.startswith("/platform/submodels")]
    client = [
        p
        for p in paths
        if p == "/submodels" or p.startswith("/submodels/")
    ]
    # Create, list, read, update, retire, publish, unpublish, import,
    # capabilities, materials (list/get/note/upload/analyze/archive),
    # train, workflows, tests, package, download, actions.
    assert len(admin) >= 20
    # Catalog, read, guidance, propose, confirm, complete, list actions.
    assert len(client) >= 6
    assert "/platform/submodels/capabilities" in paths
    assert "/platform/submodels/import" in paths
