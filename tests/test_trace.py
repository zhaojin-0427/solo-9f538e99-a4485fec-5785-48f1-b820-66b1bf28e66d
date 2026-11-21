"""End-to-end tests for the v2 trace workflow (/trace + /identify).

The scenario: after a district re-zoning migration, each period keeps both
old-side and new-side population totals. A trace package records only the
old/new coverage combinations that actually occur; /identify decides which
partition version(s) can explain a whole batch of reports via one
non-negative integer block-population vector per period.
"""
import pytest
from fastapi.testclient import TestClient

from app.builder_app import app as builder_app
from app.converter_app import app as converter_app

builder = TestClient(builder_app)
converter = TestClient(converter_app)


def trace(version, old, new):
    resp = builder.post(
        "/trace",
        json={
            "version_code": version,
            "old_districts": old,
            "new_districts": new,
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def identify(packages, reports):
    return converter.post(
        "/identify", json={"packages": packages, "reports": reports}
    )


# Two versions over the SAME district code sets but different block
# coverage. Blocks are chosen so the two versions produce distinct
# incidence structures.
#
# Version A: blocks b1..b4
#   b1: old O1        -> new N1
#   b2: old O1,O2     -> new N1
#   b3: old O2        -> new N1,N2
#   b4: old O3        -> new N2
OLD_A = [
    {"code": "O1", "blocks": ["b1", "b2"]},
    {"code": "O2", "blocks": ["b2", "b3"]},
    {"code": "O3", "blocks": ["b4"]},
]
NEW_A = [
    {"code": "N1", "blocks": ["b1", "b2", "b3"]},
    {"code": "N2", "blocks": ["b3", "b4"]},
]

# Version B: a different partition of the same blocks.
#   b1: old O1     -> new N2
#   b2: old O1,O2  -> new N1,N2
#   b3: old O2     -> new N1
#   b4: old O3     -> new N1
OLD_B = [
    {"code": "O1", "blocks": ["b1", "b2"]},
    {"code": "O2", "blocks": ["b2", "b3"]},
    {"code": "O3", "blocks": ["b4"]},
]
NEW_B = [
    {"code": "N1", "blocks": ["b2", "b3", "b4"]},
    {"code": "N2", "blocks": ["b1", "b2"]},
]


def test_trace_package_structure_and_no_block_leak():
    pkg = trace("A", OLD_A, NEW_A)
    assert pkg["schema_version"] == 2
    assert pkg["version_code"] == "A"
    assert pkg["old_districts"] == ["O1", "O2", "O3"]
    assert pkg["new_districts"] == ["N1", "N2"]
    # Four distinct blocks, four distinct coverage combinations.
    assert len(pkg["coverage_combinations"]) == 4
    # No block identifier may appear anywhere in the package.
    text = str(pkg)
    for block in ("b1", "b2", "b3", "b4"):
        assert block not in text
    # The combination for b2 (O1,O2 / N1) is recorded.
    combos = {
        (tuple(sorted(c["old_codes"])), tuple(sorted(c["new_codes"])))
        for c in pkg["coverage_combinations"]
    }
    assert (("O1", "O2"), ("N1",)) in combos
    assert (("O3",), ("N2",)) in combos


def test_identify_unique_compatible_version():
    pkg_a = trace("A", OLD_A, NEW_A)
    pkg_b = trace("B", OLD_B, NEW_B)
    # Ground-truth block populations for version A: b1=10,b2=20,b3=30,b4=40.
    # O1=30 O2=50 O3=40 ; N1=60 N2=70.
    reports = [
        {
            "period": "2021",
            "old_populations": {"O1": 30, "O2": 50, "O3": 40},
            "new_populations": {"N1": 60, "N2": 70},
        }
    ]
    resp = identify([pkg_a, pkg_b], reports)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "unique_compatible"
    assert body["compatible_versions"] == ["A"]
    by_version = {r["version_code"]: r for r in body["results"]}
    assert by_version["A"]["compatible"] is True
    assert by_version["A"]["incompatible_periods"] == []
    assert by_version["B"]["compatible"] is False
    assert by_version["B"]["incompatible_periods"] == [0]


def test_identify_no_compatible_version():
    pkg_a = trace("A", OLD_A, NEW_A)
    pkg_b = trace("B", OLD_B, NEW_B)
    # Totals that neither coverage structure can realize integrally.
    reports = [
        {
            "old_populations": {"O1": 1, "O2": 1, "O3": 1},
            "new_populations": {"N1": 0, "N2": 5},
        }
    ]
    resp = identify([pkg_a, pkg_b], reports)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "no_compatible_version"
    assert body["compatible_versions"] == []
    assert all(r["compatible"] is False for r in body["results"])
    assert all(r["incompatible_periods"] == [0] for r in body["results"])


def test_identify_multiple_compatible_versions():
    # When reports give no discriminating information (e.g. everything zero),
    # every version is compatible.
    pkg_a = trace("A", OLD_A, NEW_A)
    pkg_b = trace("B", OLD_B, NEW_B)
    reports = [
        {
            "old_populations": {"O1": 0, "O2": 0, "O3": 0},
            "new_populations": {"N1": 0, "N2": 0},
        }
    ]
    resp = identify([pkg_a, pkg_b], reports)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "multiple_compatible"
    assert set(body["compatible_versions"]) == {"A", "B"}


def test_missing_district_report_imposes_no_constraint():
    pkg_a = trace("A", OLD_A, NEW_A)
    pkg_b = trace("B", OLD_B, NEW_B)
    # Only O3 and N2 reported; every other district is missing, so those
    # totals alone must admit a non-negative solution under both versions.
    # Under A, O3 and N2 share the b4 combination; under B the same reported
    # totals can be placed on combinations without violating either row.
    reports = [
        {
            "old_populations": {"O3": 17},
            "new_populations": {"N2": 17},
        }
    ]
    resp = identify([pkg_a, pkg_b], reports)
    assert resp.status_code == 200
    body = resp.json()
    # Sparse reports do not discriminate: both versions stay compatible.
    assert body["status"] == "multiple_compatible"
    assert set(body["compatible_versions"]) == {"A", "B"}


def test_missing_report_keeps_version_feasible_that_full_report_rejects():
    pkg_a = trace("A", OLD_A, NEW_A)
    pkg_b = trace("B", OLD_B, NEW_B)
    # The full report below is only realizable under A; dropping the new-side
    # reports removes the discriminating equalities, so both versions must
    # remain compatible (missing districts impose no constraint).
    full = {
        "old_populations": {"O1": 30, "O2": 50, "O3": 40},
        "new_populations": {"N1": 60, "N2": 70},
    }
    old_only = {
        "old_populations": {"O1": 30, "O2": 50, "O3": 40},
        "new_populations": {},
    }
    resp_full = identify([pkg_a, pkg_b], [full])
    assert resp_full.json()["compatible_versions"] == ["A"]
    resp_sparse = identify([pkg_a, pkg_b], [old_only])
    assert resp_sparse.status_code == 200
    assert set(resp_sparse.json()["compatible_versions"]) == {"A", "B"}


def test_batch_uses_per_period_independent_block_populations():
    pkg_a = trace("A", OLD_A, NEW_A)
    pkg_b = trace("B", OLD_B, NEW_B)
    # Two periods with different block populations, both realizable under A.
    # Period 1: b1=1,b2=2,b3=3,b4=4 -> O1=3 O2=5 O3=4 N1=6 N2=7
    # Period 2: b1=5,b2=5,b3=5,b5=0 -> O1=10 O2=10 O3=0 N1=15 N2=5
    reports = [
        {
            "period": "p1",
            "old_populations": {"O1": 3, "O2": 5, "O3": 4},
            "new_populations": {"N1": 6, "N2": 7},
        },
        {
            "period": "p2",
            "old_populations": {"O1": 10, "O2": 10, "O3": 0},
            "new_populations": {"N1": 15, "N2": 5},
        },
    ]
    resp = identify([pkg_a, pkg_b], reports)
    assert resp.status_code == 200
    body = resp.json()
    assert body["compatible_versions"] == ["A"]
    by_version = {r["version_code"]: r for r in body["results"]}
    # B cannot realize either period; both zero-based indices are reported.
    assert by_version["B"]["incompatible_periods"] == [0, 1]


def test_incompatible_period_index_is_reported():
    pkg_a = trace("A", OLD_A, NEW_A)
    pkg_b = trace("B", OLD_B, NEW_B)
    good = {
        "old_populations": {"O1": 30, "O2": 50, "O3": 40},
        "new_populations": {"N1": 60, "N2": 70},
    }
    bad = {
        "old_populations": {"O1": 1, "O2": 1, "O3": 1},
        "new_populations": {"N1": 0, "N2": 5},
    }
    # Version A passes period 0, fails period 1.
    resp = identify([pkg_a, pkg_b], [good, bad])
    assert resp.status_code == 200
    by_version = {r["version_code"]: r for r in resp.json()["results"]}
    assert by_version["A"]["compatible"] is False
    assert by_version["A"]["incompatible_periods"] == [1]


# --------------------------------------------------------------------------
# Input validation
# --------------------------------------------------------------------------
def test_trace_rejects_duplicate_district_code():
    resp = builder.post(
        "/trace",
        json={
            "version_code": "X",
            "old_districts": [
                {"code": "O1", "blocks": ["a"]},
                {"code": "O1", "blocks": ["b"]},
            ],
            "new_districts": [{"code": "N1", "blocks": ["a"]}],
        },
    )
    assert resp.status_code == 422


def test_trace_rejects_more_than_twelve_districts():
    old = [{"code": f"O{i}", "blocks": [f"b{i}"]} for i in range(7)]
    new = [{"code": f"N{i}", "blocks": [f"b{i}"]} for i in range(6)]
    resp = builder.post(
        "/trace",
        json={"version_code": "X", "old_districts": old, "new_districts": new},
    )
    assert resp.status_code == 422


def test_identify_rejects_wrong_package_count():
    pkg = trace("A", OLD_A, NEW_A)
    reports = [
        {"old_populations": {"O1": 1}, "new_populations": {"N1": 1}}
    ]
    # Only one package: minimum is two.
    resp = identify([pkg], reports)
    assert resp.status_code == 422


def test_identify_rejects_duplicate_version_codes():
    pkg = trace("A", OLD_A, NEW_A)
    reports = [
        {"old_populations": {"O1": 1}, "new_populations": {"N1": 1}}
    ]
    resp = identify([pkg, pkg], reports)
    assert resp.status_code == 422


def test_identify_rejects_mismatched_district_sets():
    pkg_a = trace("A", OLD_A, NEW_A)
    # Build a package whose code sets differ.
    other_old = [{"code": "X9", "blocks": ["b1"]}]
    other_new = [{"code": "N1", "blocks": ["b1"]}]
    pkg_other = trace("C", other_old, other_new)
    reports = [
        {"old_populations": {"O1": 1}, "new_populations": {"N1": 1}}
    ]
    resp = identify([pkg_a, pkg_other], reports)
    assert resp.status_code == 422


def test_identify_rejects_unknown_district_in_report():
    pkg_a = trace("A", OLD_A, NEW_A)
    pkg_b = trace("B", OLD_B, NEW_B)
    reports = [
        {"old_populations": {"ZZ": 1}, "new_populations": {"N1": 1}}
    ]
    resp = identify([pkg_a, pkg_b], reports)
    assert resp.status_code == 422


def test_identify_rejects_population_out_of_range():
    pkg_a = trace("A", OLD_A, NEW_A)
    pkg_b = trace("B", OLD_B, NEW_B)
    reports = [
        {
            "old_populations": {"O1": 1_000_001},
            "new_populations": {"N1": 1},
        }
    ]
    resp = identify([pkg_a, pkg_b], reports)
    assert resp.status_code == 422
    reports_neg = [
        {"old_populations": {"O1": -1}, "new_populations": {"N1": 1}}
    ]
    resp = identify([pkg_a, pkg_b], reports_neg)
    assert resp.status_code == 422


def test_trace_package_model_caps_combinations():
    from app.models import TracePackage

    # Make every combination unique.
    combos = [
        {"old_codes": ["O1"], "new_codes": [f"N{i}"]} for i in range(65)
    ]
    with pytest.raises(ValueError):
        TracePackage(
            version_code="X",
            old_districts=["O1"],
            new_districts=[f"N{i}" for i in range(65)],
            coverage_combinations=combos,
        )


def test_empty_report_period_constrains_nothing():
    pkg_a = trace("A", OLD_A, NEW_A)
    pkg_b = trace("B", OLD_B, NEW_B)
    # A period with no reported district at all is vacuously explainable.
    resp = identify(
        [pkg_a, pkg_b],
        [{"old_populations": {}, "new_populations": {}}],
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "multiple_compatible"


def test_v1_build_and_v2_trace_coexist_on_builder():
    # The legacy /build endpoint must still produce a schema_version 1
    # package alongside the new /trace endpoint.
    resp = builder.post(
        "/build", json={"old_districts": OLD_A, "new_districts": NEW_A}
    )
    assert resp.status_code == 200
    v1 = resp.json()
    assert v1["schema_version"] == 1
    assert "old_relations" in v1
    # And an existing v1 package still flows through /convert.
    conv = converter.post(
        "/convert",
        json={"package": v1, "old_populations": {"O1": 30, "O2": 50, "O3": 40}},
    )
    assert conv.status_code == 200
    assert conv.json()["available_old_districts"] == ["O1", "O2", "O3"]


def test_engine_rejects_more_than_64_coverage_combinations():
    from app.trace_engine import TraceError, build_trace_package

    # 7 old + 5 new = 12 districts; 70 blocks with distinct membership
    # vectors yield more than 64 coverage combinations.
    old = [{"code": f"O{i}", "blocks": []} for i in range(7)]
    new = [{"code": f"N{i}", "blocks": []} for i in range(5)]
    for k in range(70):
        bk = f"b{k}"
        for i in range(7):
            if (k >> i) & 1:
                old[i]["blocks"].append(bk)
        for j in range(5):
            if (k >> (j + 7)) & 1:
                new[j]["blocks"].append(bk)
    with pytest.raises(TraceError):
        build_trace_package("TOOBIG", old, new)


def test_identify_failure_returns_error_body_only():
    # A tampered package whose combination references an unknown district is
    # rejected at validation; the endpoint never returns partial results.
    pkg_a = trace("A", OLD_A, NEW_A)
    pkg_b = trace("B", OLD_B, NEW_B)
    pkg_b["old_districts"] = ["O1", "O2"]  # drop O3 but keep references
    resp = identify(
        [pkg_a, pkg_b],
        [{"old_populations": {"O1": 1}, "new_populations": {"N1": 1}}],
    )
    assert resp.status_code == 422
    body = resp.json()
    assert "detail" in body
