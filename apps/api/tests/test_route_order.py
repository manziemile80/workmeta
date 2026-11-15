"""The application must not serve routes for removed subsystems.

FastAPI matches routes in the order they are registered, so a router that is
left behind after its feature is deleted keeps answering. These tests pin the
routes that are gone.
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


def test_the_trained_extension_feature_is_gone() -> None:
    """The extension subsystem was removed; nothing serves it any more."""
    paths = _paths()
    assert not [path for path in paths if "/extensions" in path]


def test_the_submodel_feature_is_gone() -> None:
    """The submodel subsystem was removed; nothing serves it any more."""
    paths = _paths()
    assert not [path for path in paths if "submodel" in path]
