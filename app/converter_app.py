"""FastAPI instance #2: convert populations and identify partition versions."""
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from .engine import ConversionError, convert
from .models import (
    ConvertRequest,
    ConvertResponse,
    ErrorResponse,
    IdentifyRequest,
    IdentifyResponse,
)
from .trace_engine import TraceError, identify_versions

app = FastAPI(
    title="District Re-zoning Population Converter",
    description=(
        "Two independent workflows are served here.\n\n"
        "1. `POST /convert` — submit a v1 handoff package with the currently "
        "available old-district populations (non-negative integers) and "
        "receive the new-district populations derivable exactly, with their "
        "fractional-coefficient formulas; every other new district is marked "
        "as missing data.\n"
        "2. `POST /identify` — submit 2-6 v2 trace packages (one per "
        "candidate partition version) and 1-6 period reports containing both "
        "old- and new-side totals. Every version whose coverage combinations "
        "can explain all reports with one non-negative integer "
        "block-population vector per period is listed, and the batch is "
        "labelled uniquely/multiply/not compatible."
    ),
    version="2.0.0",
)


@app.exception_handler(ConversionError)
def _conversion_error_handler(_request, exc: ConversionError):
    return JSONResponse(
        status_code=400,
        content=ErrorResponse(error="conversion_failed", detail=str(exc)).model_dump(),
    )


@app.exception_handler(TraceError)
def _trace_error_handler(_request, exc: TraceError):
    return JSONResponse(
        status_code=400,
        content=ErrorResponse(error="identification_failed", detail=str(exc)).model_dump(),
    )


@app.get("/", tags=["meta"])
def root():
    return {
        "service": "population-converter",
        "docs": "/docs",
        "endpoints": ["/convert", "/identify"],
    }


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok"}


@app.post(
    "/convert",
    response_model=ConvertResponse,
    responses={400: {"model": ErrorResponse}},
    tags=["convert"],
    summary="Convert known old populations into new-district populations (v1)",
)
def run_convert(request: ConvertRequest) -> ConvertResponse:
    result = convert(
        package=request.package.model_dump(),
        old_populations=dict(request.old_populations),
    )
    return ConvertResponse(**result)


@app.post(
    "/identify",
    response_model=IdentifyResponse,
    responses={400: {"model": ErrorResponse}},
    tags=["identify"],
    summary="Identify every partition version compatible with the batch of reports (v2)",
)
def run_identify(request: IdentifyRequest) -> IdentifyResponse:
    result = identify_versions(
        packages=[p.model_dump() for p in request.packages],
        reports=[r.model_dump() for r in request.reports],
    )
    return IdentifyResponse(**result)
