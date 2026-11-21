"""FastAPI instance #1: build the block-free handoff packages."""
from fastapi import FastAPI, HTTPException

from .engine import build_package
from .models import BuildRequest, HandoffPackage, TraceBuildRequest, TracePackage
from .trace_engine import TraceError, build_trace_package

app = FastAPI(
    title="District Re-zoning Package Builder",
    description=(
        "Submit old/new district codes together with the street-block IDs "
        "each district covers (a block may belong to several districts).\n\n"
        "Two package flavours are produced, neither retaining block IDs or "
        "per-block memberships:\n"
        "- `POST /build` — v1 package with the exact algebraic relations "
        "between district populations;\n"
        "- `POST /trace` — v2 trace package for a named partition version, "
        "recording only the old/new district-code coverage combinations that "
        "actually occur."
    ),
    version="2.0.0",
)


@app.get("/", tags=["meta"])
def root():
    return {
        "service": "package-builder",
        "docs": "/docs",
        "endpoints": ["/build", "/trace"],
    }


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok"}


@app.post(
    "/build",
    response_model=HandoffPackage,
    tags=["build"],
    summary="Build the v1 JSON handoff package",
)
def build(request: BuildRequest) -> HandoffPackage:
    package = build_package(
        old_districts=[d.model_dump() for d in request.old_districts],
        new_districts=[d.model_dump() for d in request.new_districts],
    )
    return HandoffPackage(**package)


@app.post(
    "/trace",
    response_model=TracePackage,
    tags=["trace"],
    summary="Build the v2 block-free trace package for a partition version",
    responses={400: {"description": "Trace package limits violated"}},
)
def build_trace(request: TraceBuildRequest) -> TracePackage:
    try:
        package = build_trace_package(
            version_code=request.version_code,
            old_districts=[d.model_dump() for d in request.old_districts],
            new_districts=[d.model_dump() for d in request.new_districts],
        )
    except TraceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return TracePackage(**package)
