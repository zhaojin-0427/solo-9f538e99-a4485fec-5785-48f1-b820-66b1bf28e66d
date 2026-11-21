"""Domain logic for v2 trace packages and version identification.

A trace package forgets street-block identities entirely.  For every block
only the *coverage combination* matters — the pair of old/new district-code
sets whose members cover the block — so the package stores the distinct
combinations that actually occur, nothing else.

Given period reports, a version is compatible exactly when one single
vector of non-negative integer block populations (one population per
coverage combination; populations may differ between periods) reproduces
every reported district total on both sides simultaneously.  The exact
decision lives in :mod:`app.ilp`.
"""
from typing import Any, Dict, List, Tuple

from .ilp import FeasibilityError, integer_feasible
from .models import MAX_COVERAGE_COMBINATIONS


class TraceError(ValueError):
    """Raised when a v2 request is semantically invalid or cannot be solved."""


# --------------------------------------------------------------------------
# Builder
# --------------------------------------------------------------------------
def build_trace_package(
    version_code: str,
    old_districts: List[Dict[str, Any]],
    new_districts: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Build a block-free v2 trace package.

    Each district is ``{"code": str, "blocks": [block_id, ...]}``.
    """
    old_codes = [d["code"] for d in old_districts]
    new_codes = [d["code"] for d in new_districts]
    old_lookup = {d["code"]: set(d["blocks"]) for d in old_districts}
    new_lookup = {d["code"]: set(d["blocks"]) for d in new_districts}
    all_blocks = sorted(
        {b for d in old_districts + new_districts for b in d["blocks"]}
    )

    combinations = set()
    for block in all_blocks:
        old_here = tuple(
            sorted(code for code, blocks in old_lookup.items() if block in blocks)
        )
        new_here = tuple(
            sorted(code for code, blocks in new_lookup.items() if block in blocks)
        )
        # A block covered by no district at all carries no information.
        if old_here or new_here:
            combinations.add((old_here, new_here))

    if len(combinations) > MAX_COVERAGE_COMBINATIONS:
        raise TraceError(
            f"version {version_code!r} exhibits {len(combinations)} coverage "
            f"combinations; at most {MAX_COVERAGE_COMBINATIONS} are allowed"
        )

    ordered = sorted(combinations)
    return {
        "schema_version": 2,
        "version_code": version_code,
        "old_districts": old_codes,
        "new_districts": new_codes,
        "coverage_combinations": [
            {"old_codes": list(old_here), "new_codes": list(new_here)}
            for old_here, new_here in ordered
        ],
    }


# --------------------------------------------------------------------------
# Version identification
# --------------------------------------------------------------------------
def _period_system(
    package: Dict[str, Any],
    old_populations: Dict[str, int],
    new_populations: Dict[str, int],
) -> Tuple[List[List[int]], List[int], List[str]]:
    """Assemble the incidence system ``A y = b`` for one period.

    Rows are the reporting districts (old side first, then new side);
    columns are the package's coverage combinations, one integer variable
    per combination holding the total population of blocks exhibiting it.
    """
    combos = package["coverage_combinations"]
    reported: List[Tuple[str, int]] = []
    for code in package["old_districts"]:
        if code in old_populations:
            reported.append((code, old_populations[code]))
    for code in package["new_districts"]:
        if code in new_populations:
            reported.append((code, new_populations[code]))

    matrix: List[List[int]] = []
    rhs: List[int] = []
    row_labels: List[str] = []
    for label, total in reported:
        row = [
            1
            if label in combo["old_codes"] or label in combo["new_codes"]
            else 0
            for combo in combos
        ]
        matrix.append(row)
        rhs.append(total)
        row_labels.append(label)
    return matrix, rhs, row_labels


def _version_explains(package: Dict[str, Any], reports: List[Dict[str, Any]]) -> List[int]:
    """Return zero-based indices of reports this version cannot explain."""
    incompatible: List[int] = []
    for idx, report in enumerate(reports):
        matrix, rhs, _labels = _period_system(
            package,
            dict(report["old_populations"]),
            dict(report["new_populations"]),
        )
        # A reported district covered by no combination can only report zero.
        if not matrix:
            if any(v != 0 for v in rhs):
                incompatible.append(idx)
            continue
        try:
            if not integer_feasible(matrix, rhs):
                incompatible.append(idx)
        except FeasibilityError as exc:
            raise TraceError(
                f"version {package['version_code']!r}, report #{idx}: {exc}"
            ) from exc
    return incompatible


def identify_versions(
    packages: List[Dict[str, Any]], reports: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Check every candidate version against the whole batch of reports."""
    results = []
    for package in packages:
        bad_periods = _version_explains(package, reports)
        results.append(
            {
                "version_code": package["version_code"],
                "compatible": not bad_periods,
                "incompatible_periods": bad_periods,
            }
        )

    compatible_versions = [r["version_code"] for r in results if r["compatible"]]
    if len(compatible_versions) == 1:
        status = "unique_compatible"
    elif len(compatible_versions) > 1:
        status = "multiple_compatible"
    else:
        status = "no_compatible_version"
    return {
        "status": status,
        "compatible_versions": compatible_versions,
        "results": results,
    }
