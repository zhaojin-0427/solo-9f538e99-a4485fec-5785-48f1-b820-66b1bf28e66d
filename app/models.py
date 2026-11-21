"""Pydantic request/response models (shared by both FastAPI apps)."""
from typing import Annotated, Dict, List, Optional

from pydantic import BaseModel, Field, model_validator

# Hard limits stated by the v2 trace workflow.
MAX_DISTRICTS_TOTAL = 12
MAX_COVERAGE_COMBINATIONS = 64
MAX_CANDIDATE_PACKAGES = 6
MIN_CANDIDATE_PACKAGES = 2
MAX_PERIOD_REPORTS = 6
MIN_PERIOD_REPORTS = 1
MAX_POPULATION = 1_000_000

Population = Annotated[int, Field(ge=0, le=MAX_POPULATION)]


# --------------------------------------------------------------------------
# Handoff package builder (/build)
# --------------------------------------------------------------------------
class DistrictInput(BaseModel):
    code: str = Field(..., min_length=1, description="District code (old or new).")
    blocks: List[str] = Field(
        ..., description="IDs of the street blocks covered by this district."
    )


class BuildRequest(BaseModel):
    old_districts: List[DistrictInput] = Field(
        ..., description="Old districts with the street blocks each covers."
    )
    new_districts: List[DistrictInput] = Field(
        ..., description="New districts with the street blocks each covers."
    )

    @model_validator(mode="after")
    def _validate(self):
        for label, districts in (
            ("old_districts", self.old_districts),
            ("new_districts", self.new_districts),
        ):
            if not districts:
                raise ValueError(f"{label} must not be empty")
            codes = [d.code for d in districts]
            if len(set(codes)) != len(codes):
                raise ValueError(f"duplicate district code in {label}")
            for d in districts:
                if len(d.blocks) != len(set(d.blocks)):
                    raise ValueError(f"duplicate block id in district {d.code}")
        return self


class NewDistrictPackage(BaseModel):
    code: str
    derivable: bool = Field(
        ..., description="Whether this new district is exactly expressible "
        "in terms of old districts."
    )
    coefficients: Dict[str, str] = Field(
        default_factory=dict,
        description="Exact fraction coefficients keyed by old district code "
        '(e.g. "2/3"); whole numbers look like "2".',
    )


class HandoffPackage(BaseModel):
    schema_version: int = 1
    old_districts: List[str]
    new_districts: List[str]
    old_relations: List[Dict[str, int]] = Field(
        ...,
        description="Exact linear equations that old-district populations "
        "must satisfy. Each entry maps old code -> integer coefficient; "
        "the weighted sum must equal 0.",
    )
    new_district_relations: List[NewDistrictPackage]

    @model_validator(mode="after")
    def _validate(self):
        old_set = set(self.old_districts)
        if len(old_set) != len(self.old_districts):
            raise ValueError("duplicate old district code in package")
        new_set = set(self.new_districts)
        if len(new_set) != len(self.new_districts):
            raise ValueError("duplicate new district code in package")
        for rel in self.old_relations:
            if not rel:
                raise ValueError("empty old relation")
            unknown = set(rel) - old_set
            if unknown:
                raise ValueError(f"old relation references unknown districts: {sorted(unknown)}")
            if all(v == 0 for v in rel.values()):
                raise ValueError("all-zero old relation")
        for item in self.new_district_relations:
            if item.code not in new_set:
                raise ValueError(f"unknown new district {item.code}")
            unknown = set(item.coefficients) - old_set
            if unknown:
                raise ValueError(
                    f"coefficient for unknown old district: {sorted(unknown)}"
                )
        if {d.code for d in self.new_district_relations} != new_set:
            raise ValueError("new_district_relations must cover every new district exactly once")
        return self


# --------------------------------------------------------------------------
# Converter (/convert)
# --------------------------------------------------------------------------
class ConvertRequest(BaseModel):
    package: HandoffPackage
    old_populations: Dict[str, int] = Field(
        ..., description="Old-district populations currently available "
        "(non-negative integers). Missing codes mean 'no data'."
    )

    @model_validator(mode="after")
    def _validate(self):
        known = set(self.package.old_districts)
        unknown = set(self.old_populations) - known
        if unknown:
            raise ValueError(f"unknown old district code(s): {sorted(unknown)}")
        negatives = [c for c, v in self.old_populations.items() if v < 0]
        if negatives:
            raise ValueError(f"populations must be non-negative integers: {negatives}")
        return self


class CheckedRelation(BaseModel):
    relation: Dict[str, int]
    expression: str
    evaluated_value: int
    satisfied: bool


class NewDistrictResult(BaseModel):
    code: str
    status: str = Field(..., description='"computed" or "missing"')
    population: Optional[int] = None
    coefficients: Dict[str, str] = Field(default_factory=dict)
    expression: Optional[str] = None
    missing_reason: Optional[str] = None


class ConvertResponse(BaseModel):
    available_old_districts: List[str]
    checked_relations: List[CheckedRelation]
    computed: List[NewDistrictResult]
    missing: List[NewDistrictResult]
    results: List[NewDistrictResult]


class ErrorResponse(BaseModel):
    error: str
    detail: Optional[str] = None


# --------------------------------------------------------------------------
# Trace package builder (v2, POST /trace)
#
# A trace package keeps, for a partition version, only the coverage
# combinations that actually occur: for each such combination the old and
# new district codes of every block exhibiting it. No block IDs, no block
# counts and no per-block memberships are retained.
# --------------------------------------------------------------------------
class TraceBuildRequest(BaseModel):
    version_code: str = Field(
        ..., min_length=1, description="Partition version identifier."
    )
    old_districts: List[DistrictInput] = Field(
        ..., description="Old districts with the street blocks each covers."
    )
    new_districts: List[DistrictInput] = Field(
        ..., description="New districts with the street blocks each covers."
    )

    @model_validator(mode="after")
    def _validate(self):
        for label, districts in (
            ("old_districts", self.old_districts),
            ("new_districts", self.new_districts),
        ):
            if not districts:
                raise ValueError(f"{label} must not be empty")
            codes = [d.code for d in districts]
            if len(set(codes)) != len(codes):
                raise ValueError(f"duplicate district code in {label}")
            for d in districts:
                if len(d.blocks) != len(set(d.blocks)):
                    raise ValueError(f"duplicate block id in district {d.code}")
        if (
            len(self.old_districts) + len(self.new_districts)
            > MAX_DISTRICTS_TOTAL
        ):
            raise ValueError(
                "old and new districts together must number at most "
                f"{MAX_DISTRICTS_TOTAL}"
            )
        return self


class CoverageCombination(BaseModel):
    old_codes: List[str] = Field(
        ..., description="Old districts covering every block of this combination."
    )
    new_codes: List[str] = Field(
        ..., description="New districts covering every block of this combination."
    )


class TracePackage(BaseModel):
    schema_version: int = 2
    version_code: str
    old_districts: List[str]
    new_districts: List[str]
    coverage_combinations: List[CoverageCombination] = Field(
        ...,
        description="Distinct (old code set, new code set) pairs actually "
        f"exhibited by blocks; at most {MAX_COVERAGE_COMBINATIONS} entries.",
    )

    @model_validator(mode="after")
    def _validate(self):
        if self.schema_version != 2:
            raise ValueError("trace package requires schema_version 2")
        old_set = set(self.old_districts)
        new_set = set(self.new_districts)
        if len(old_set) != len(self.old_districts):
            raise ValueError("duplicate old district code in package")
        if len(new_set) != len(self.new_districts):
            raise ValueError("duplicate new district code in package")
        if len(old_set) + len(new_set) > MAX_DISTRICTS_TOTAL:
            raise ValueError(
                "old and new districts together must number at most "
                f"{MAX_DISTRICTS_TOTAL}"
            )
        if len(self.coverage_combinations) > MAX_COVERAGE_COMBINATIONS:
            raise ValueError(
                "a version may contain at most "
                f"{MAX_COVERAGE_COMBINATIONS} coverage combinations"
            )
        seen = set()
        for combo in self.coverage_combinations:
            if not combo.old_codes and not combo.new_codes:
                raise ValueError(
                    "coverage combination must reference at least one district"
                )
            if len(set(combo.old_codes)) != len(combo.old_codes):
                raise ValueError("duplicate old code in coverage combination")
            if len(set(combo.new_codes)) != len(combo.new_codes):
                raise ValueError("duplicate new code in coverage combination")
            unknown_old = set(combo.old_codes) - old_set
            unknown_new = set(combo.new_codes) - new_set
            if unknown_old:
                raise ValueError(
                    f"coverage combination references unknown old district: "
                    f"{sorted(unknown_old)}"
                )
            if unknown_new:
                raise ValueError(
                    f"coverage combination references unknown new district: "
                    f"{sorted(unknown_new)}"
                )
            key = (tuple(sorted(combo.old_codes)), tuple(sorted(combo.new_codes)))
            if key in seen:
                raise ValueError(f"duplicate coverage combination: {key}")
            seen.add(key)
        return self


# --------------------------------------------------------------------------
# Version identification (v2, POST /identify)
# --------------------------------------------------------------------------
class PeriodReport(BaseModel):
    period: Optional[str] = Field(
        None, description="Optional period label (free text)."
    )
    old_populations: Dict[str, Population] = Field(
        default_factory=dict,
        description="Reported old-district populations for this period; "
        "missing codes mean 'no report' and impose no constraint.",
    )
    new_populations: Dict[str, Population] = Field(
        default_factory=dict,
        description="Reported new-district populations for this period.",
    )


class IdentifyRequest(BaseModel):
    packages: List[TracePackage] = Field(
        ...,
        description=f"{MIN_CANDIDATE_PACKAGES}-{MAX_CANDIDATE_PACKAGES} "
        "candidate trace packages of different version codes.",
    )
    reports: List[PeriodReport] = Field(
        ...,
        description=f"{MIN_PERIOD_REPORTS}-{MAX_PERIOD_REPORTS} period reports.",
    )

    @model_validator(mode="after")
    def _validate(self):
        if not (MIN_CANDIDATE_PACKAGES <= len(self.packages)
                <= MAX_CANDIDATE_PACKAGES):
            raise ValueError(
                f"expected between {MIN_CANDIDATE_PACKAGES} and "
                f"{MAX_CANDIDATE_PACKAGES} candidate packages"
            )
        if not (MIN_PERIOD_REPORTS <= len(self.reports)
                <= MAX_PERIOD_REPORTS):
            raise ValueError(
                f"expected between {MIN_PERIOD_REPORTS} and "
                f"{MAX_PERIOD_REPORTS} period reports"
            )
        versions = [p.version_code for p in self.packages]
        if len(set(versions)) != len(versions):
            raise ValueError("candidate packages must have distinct version codes")
        old_sets = {tuple(p.old_districts) for p in self.packages}
        new_sets = {tuple(p.new_districts) for p in self.packages}
        if len(old_sets) != 1:
            raise ValueError(
                "all candidate packages must share the same old district code set"
            )
        if len(new_sets) != 1:
            raise ValueError(
                "all candidate packages must share the same new district code set"
            )
        old_codes = set(self.packages[0].old_districts)
        new_codes = set(self.packages[0].new_districts)
        for k, report in enumerate(self.reports):
            unknown_old = set(report.old_populations) - old_codes
            unknown_new = set(report.new_populations) - new_codes
            if unknown_old:
                raise ValueError(
                    f"report #{k} references unknown old district code(s): "
                    f"{sorted(unknown_old)}"
                )
            if unknown_new:
                raise ValueError(
                    f"report #{k} references unknown new district code(s): "
                    f"{sorted(unknown_new)}"
                )
        return self


class VersionCompatibility(BaseModel):
    version_code: str
    compatible: bool
    incompatible_periods: List[int] = Field(
        default_factory=list,
        description="Zero-based indices of reports that no non-negative "
        "integer block-population vector can explain under this version.",
    )


class IdentifyResponse(BaseModel):
    status: str = Field(
        ...,
        description='"unique_compatible", "multiple_compatible" or '
        '"no_compatible_version".',
    )
    compatible_versions: List[str]
    results: List[VersionCompatibility] = Field(
        ..., description="One entry per candidate package, in request order."
    )
