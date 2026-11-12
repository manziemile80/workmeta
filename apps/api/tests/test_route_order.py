"""The routes that are a literal word must not be shadowed by a path parameter.

FastAPI matches routes in the order they are registered, so a route such as
``/limits`` that is registered after ``/{extension_id}`` is never reached:
the parameter route answers first and the word is read as an identifier.
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


def test_the_literal_routes_exist() -> None:
    paths = _paths()
    for literal in (
        "/organizations/{org_id}/extensions/limits",
        "/organizations/{org_id}/extensions/recognize",
        "/organizations/{org_id}/submodels/limits",
        "/organizations/{org_id}/submodels/import",
    ):
        assert literal in paths, f"{literal} is missing"


def test_the_limits_route_is_not_shadowed_by_the_extension_id() -> None:
    paths = _paths()
    assert paths.index("/organizations/{org_id}/extensions/limits") < paths.index(
        "/organizations/{org_id}/extensions/{extension_id}"
    )


def test_the_limits_route_is_not_shadowed_by_the_submodel_id() -> None:
    paths = _paths()
    assert paths.index("/organizations/{org_id}/submodels/limits") < paths.index(
        "/organizations/{org_id}/submodels/{submodel_id}"
    )


def test_the_recognize_route_is_not_shadowed() -> None:
    paths = _paths()
    assert paths.index(
        "/organizations/{org_id}/extensions/recognize"
    ) < paths.index("/organizations/{org_id}/extensions/{extension_id}")


def test_the_import_route_is_not_shadowed() -> None:
    """``POST /submodels/import`` must not be read as a submodel id.

    The router has a ``GET /{submodel_id}`` route, which matches the path of
    the import but not its method. The framework remembers that as a partial
    match and keeps looking, so the import route still wins — but only
    because there is no POST route on the identifier itself.
    """
    paths = _paths()
    assert "/organizations/{org_id}/submodels/import" in paths
    post_paths = [
        route.path
        for route in _routes()
        if "POST" in (route.methods or set())
    ]
    assert "/organizations/{org_id}/submodels/{submodel_id}" not in post_paths
