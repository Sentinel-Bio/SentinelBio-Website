"""Sentinel Bio — FastAPI entrypoint."""
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.auth import OptionalUser, RequireUser
from app.config import get_settings
from app.rate_limit import limiter
from app.routes import admin, projects, species
from app.routes import tools as tools_router
from app.routes import files as files_router
from app.routes import members as members_router

settings = get_settings()

app = FastAPI(title="Sentinel Bio", version="0.1.0")

# Rate limiter — slowapi needs this wired into state and middleware.
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.middleware("http")
async def attach_user_to_request_state(request: Request, call_next):
    """Decode the JWT once per request so the rate limiter can key by user_id."""
    from app.auth import try_user
    auth_header = request.headers.get("authorization")
    request.state.user = try_user(auth_header)
    return await call_next(request)


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/me")
async def me(user: OptionalUser) -> dict[str, object]:
    if user is None:
        return {"authenticated": False}
    return {"authenticated": True, "user_id": user.id, "email": user.email}


@app.get("/protected")
async def protected(user: RequireUser) -> dict[str, str]:
    return {"message": f"Hello, {user.email or user.id}"}


app.include_router(species.router, prefix="/api")
app.include_router(projects.router, prefix="/api")
app.include_router(admin.router, prefix="/api")
app.include_router(tools_router.router, prefix="/api")
app.include_router(files_router.router, prefix="/api")
app.include_router(members_router.router, prefix="/api")

@app.middleware("http")
async def legacy_collections_redirect(request: Request, call_next):
    """Redirect /api/collections/* to /api/projects/* for backwards compat."""
    if request.url.path.startswith("/api/collections"):
        new_path = request.url.path.replace("/api/collections", "/api/projects", 1)
        query = f"?{request.url.query}" if request.url.query else ""
        return RedirectResponse(url=f"{new_path}{query}", status_code=307)
    return await call_next(request)
