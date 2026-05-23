"""Smoke tests.

The single most important test in the suite. Catches the class of bug
where a route declaration, schema field, or import statement is invalid
in a way that crashes the process at startup but passes lint locally.

Real history: two prod outages on 2026-05-22 would have been blocked
by this test alone. One was `EmailStr` requiring an unlisted package;
the other was FastAPI 0.115's stricter validation rejecting
`status_code=204` on handlers with a `Response` parameter. Both failed
on `from app.main import app`.
"""


def test_app_imports():
    """Just importing should be enough to surface the most common
    structural errors — bad type annotations, route declaration
    violations, missing deps. If this passes, the container can start."""
    from app.main import app

    # Trivial assertion to silence "test does nothing" lint, but the
    # real value is everything above it not raising.
    assert app is not None


def test_openapi_renders():
    """One step beyond import: FastAPI can generate its OpenAPI schema.
    This catches a broader class of schema errors (e.g. a Pydantic model
    with conflicting field constraints) than plain import alone.
    """
    from app.main import app

    schema = app.openapi()
    assert "paths" in schema
    assert "/auth/login" in schema["paths"]
    assert "/auth/me" in schema["paths"]
    assert "/devices" in schema["paths"]
