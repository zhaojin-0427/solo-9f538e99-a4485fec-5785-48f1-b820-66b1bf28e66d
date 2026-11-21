"""Exact feasibility of non-negative integer linear systems ``A y = b``.

The trace packages describe street blocks only through the *coverage
combinations* that actually occur.  A period's reports are therefore
explainable exactly when there exist non-negative integer populations
``y_k`` (one per coverage combination) such that

    A y = b,   y_k >= 0, integral,

where ``A`` is the 0/1 incidence matrix of the combinations against every
old and new district and ``b`` holds the reported populations.

This module decides that question exactly — no floating point is used
anywhere.  The solver is a branch-and-bound search:

* **bound propagation** in pure integer arithmetic tightens lower/upper
  bounds per variable from each row (every coefficient is 0 or 1);
* the linear relaxation is solved with an exact canonical two-phase
  simplex built on :class:`fractions.Fraction`;
* a fractional relaxation value triggers a classic floor/ceil branch.

The instances here are small (at most 12 rows, 64 columns, right-hand
sides up to 1,000,000), so exact arithmetic stays comfortably fast.
"""
from fractions import Fraction
from typing import Dict, List, Optional, Sequence, Tuple


class FeasibilityError(RuntimeError):
    """Raised when the search cannot be completed (e.g. pivot budget)."""


# --------------------------------------------------------------------------
# Integer bound propagation
# --------------------------------------------------------------------------
def _propagate(
    a: List[List[int]],
    b: List[int],
    lower: List[int],
    upper: List[Optional[int]],
) -> Optional[Tuple[List[int], List[Optional[int]]]]:
    """Tighten non-negative integer bounds using rows of 0/1 coefficients.

    Returns the new bounds, or ``None`` when the box is already empty.
    """
    m = len(a)
    n = len(a[0])
    lo = lower[:]
    hi = upper[:]
    rows: List[Tuple[int, List[int]]] = [
        (b[i], [j for j in range(n) if a[i][j]]) for i in range(m)
    ]

    changed = True
    while changed:
        changed = False
        for bi, cols in rows:
            fixed = sum(lo[j] for j in cols if hi[j] == lo[j])
            free = [j for j in cols if hi[j] != lo[j]]
            lo_sum = sum(lo[j] for j in free)
            hi_sum = sum(hi[j] for j in free if hi[j] is not None)
            unbounded = any(hi[j] is None for j in free)

            if fixed + lo_sum > bi:
                return None
            if not unbounded and fixed + hi_sum < bi:
                return None
            residual = bi - fixed

            for j in free:
                others_lo = lo_sum - lo[j]
                cap = residual - others_lo
                if cap < lo[j]:
                    return None
                if hi[j] is None or cap < hi[j]:
                    hi[j] = cap
                    changed = True
                if not unbounded:
                    others_hi = hi_sum - (hi[j] if hi[j] is not None else 0)
                    new_lo = residual - others_hi
                    if new_lo > lo[j]:
                        if new_lo > (hi[j] if hi[j] is not None else new_lo):
                            return None
                        lo[j] = new_lo
                        changed = True
                if hi[j] is not None and lo[j] > hi[j]:
                    return None
    return lo, hi


# --------------------------------------------------------------------------
# Exact linear relaxation (canonical two-phase simplex, Phase I)
#
# One Gauss-Jordan tableau has one column per variable *label* plus a
# trailing RHS column.  ``basic[i]`` names the variable held by row ``i``;
# ``nonbasic`` lists the remaining labels.  The Phase I objective row is the
# last row and is pivoted together with the constraint rows.
#
# Convention: Phase I maximizes  z = -w  where w is the sum of artificial
# variables.  An improving (entering) non-basic, non-artificial column is
# therefore one whose objective coefficient is negative.  Bland's rule on
# variable labels guarantees finite termination even at degenerate vertices.
# --------------------------------------------------------------------------
def _gauss_jordan(
    rows: List[List[Fraction]], enter_label: int, leave: int
) -> None:
    """Pivot every tableau row on ``rows[leave][enter_label]`` in place."""
    pivot_value = rows[leave][enter_label]
    rows[leave] = [x / pivot_value for x in rows[leave]]
    pivot_row = rows[leave]
    for i in range(len(rows)):
        if i == leave:
            continue
        factor = rows[i][enter_label]
        if factor != 0:
            rows[i] = [x - factor * y for x, y in zip(rows[i], pivot_row)]


def _lp_relaxation(
    a: List[List[int]],
    b: List[int],
    lower: List[int],
    upper: List[Optional[int]],
    pivot_budget: int = 100_000,
) -> Tuple[bool, Optional[List[Fraction]]]:
    """Feasibility of ``A y = b`` with box bounds, relaxed to rationals.

    Returns ``(feasible, solution)``; the solution honours every bound.
    """
    m = len(a)
    n = len(a[0])
    cols = list(range(n))

    # Shift by the lower bounds: t_j = y_j - lower_j >= 0.
    shifted_rhs = [b[i] - sum(a[i][j] * lower[j] for j in cols) for i in range(m)]
    if any(rhs < 0 for rhs in shifted_rhs):
        return False, None

    # Variable labels: structural 0..n-1, then artificials, then slacks.
    # Finite upper bounds become  t_j + s_j = u_j - l_j  with a basic slack.
    bounded = [j for j in cols if upper[j] is not None]
    art0 = n
    n_art = m
    slack0 = art0 + n_art
    slack_label = {j: slack0 + k for k, j in enumerate(bounded)}
    n_total = slack0 + len(bounded)
    rhs_col = n_total

    tableau: List[List[Fraction]] = []
    for i in range(m):
        row = [Fraction(0) for _ in range(n_total + 1)]
        for j in cols:
            row[j] = Fraction(a[i][j])
        row[art0 + i] = Fraction(1)
        row[rhs_col] = Fraction(shifted_rhs[i])
        tableau.append(row)
    for j in bounded:
        row = [Fraction(0) for _ in range(n_total + 1)]
        row[j] = Fraction(1)
        row[slack_label[j]] = Fraction(1)
        row[rhs_col] = Fraction(upper[j] - lower[j])
        tableau.append(row)
    n_constraint_rows = len(tableau)

    basic = [art0 + i for i in range(m)] + [
        slack_label[j] for j in bounded
    ]
    nonbasic = [v for v in range(n_total) if v not in set(basic)]

    # Objective row for z = -w:  z + sum(art) = 0. Eliminate the initially
    # basic artificial columns by subtracting the first m constraint rows.
    obj_row = [Fraction(0) for _ in range(n_total + 1)]
    for k in range(n_art):
        obj_row[art0 + k] = Fraction(1)
    for i in range(m):
        obj_row = [x - y for x, y in zip(obj_row, tableau[i])]
    tableau.append(obj_row)

    pivots = 0
    while True:
        entering_labels = [
            label
            for label in nonbasic
            if label < art0 and tableau[-1][label] < 0
        ]
        if entering_labels:
            enter_label = min(entering_labels)  # Bland's rule.
            ratio_candidates: List[Tuple[Fraction, int, int]] = []
            for i in range(n_constraint_rows):
                coef = tableau[i][enter_label]
                if coef <= 0:
                    continue
                ratio_candidates.append(
                    (tableau[i][rhs_col] / coef, basic[i], i)
                )
            if not ratio_candidates:
                raise FeasibilityError(
                    "phase-I simplex failed: unbounded objective"
                )
            _, _, leave_row = min(ratio_candidates)  # Bland's tie-break.
        else:
            # No improving structural column. Standard Phase I must still
            # drive every artificial out of the basis before declaring the
            # optimum. Pick an artificial row and pivot on any non-artificial
            # non-zero entry (a slack column is allowed here).
            art_row = next(
                (
                    i
                    for i in range(n_constraint_rows)
                    if art0 <= basic[i] < slack0
                ),
                None,
            )
            if art_row is None:
                break  # optimum reached with no artificial in the basis.
            # A positive pivot coefficient keeps the row's RHS
            # non-negative; prefer such a column. Only if none exists do we
            # accept a negative coefficient (then the row's RHS is zero).
            eligible = [
                label
                for label in nonbasic
                if (label < art0 or label >= slack0)
                and tableau[art_row][label] != 0
            ]
            positive = [
                label for label in eligible if tableau[art_row][label] > 0
            ]
            if positive:
                enter_label = min(positive)
            elif eligible and tableau[art_row][rhs_col] == 0:
                enter_label = min(eligible)
            else:
                # The row cannot be made non-artificial feasibly. It is
                # harmless only when its RHS is zero; otherwise it encodes
                # the contradiction 0 = (non-zero RHS).
                if tableau[art_row][rhs_col] != 0:
                    return False, None
                break
            leave_row = art_row

        pivots += 1
        if pivots > pivot_budget:
            raise FeasibilityError("phase-I simplex exceeded its pivot budget")
        _gauss_jordan(tableau, enter_label, leave_row)
        leaving_label = basic[leave_row]
        basic[leave_row] = enter_label
        nonbasic[nonbasic.index(enter_label)] = leaving_label

    # z* = 0  <=>  w* = 0  <=>  a feasible point exists.
    if tableau[-1][rhs_col] != 0:
        return False, None

    values: Dict[int, Fraction] = {}
    for i, label in enumerate(basic):
        if label < n:
            values[label] = tableau[i][rhs_col]
    solution = [
        Fraction(lower[j]) + values.get(j, Fraction(0)) for j in cols
    ]
    # Defensive correctness check: the returned point must honour every
    # equality and every box bound. The dictionary construction guarantees
    # this, but verifying here makes any pivot bookkeeping error fail loud.
    for i in range(m):
        if sum(a[i][j] * solution[j] for j in cols) != b[i]:
            return False, None
    for j in cols:
        if solution[j] < lower[j]:
            return False, None
        if upper[j] is not None and solution[j] > upper[j]:
            return False, None
    return True, solution


# --------------------------------------------------------------------------
# Branch and bound
# --------------------------------------------------------------------------
def integer_feasible(
    matrix: Sequence[Sequence[int]],
    rhs: Sequence[int],
    node_budget: int = 100_000,
) -> bool:
    """Decide whether ``A y = b`` has a non-negative integral solution.

    Entries of ``matrix`` must be 0/1 and entries of ``rhs`` non-negative.
    Raises :class:`FeasibilityError` if the search exhausts its budget.
    """
    a = [list(map(int, row)) for row in matrix]
    b = [int(x) for x in rhs]
    if not a:
        return all(x == 0 for x in b)
    m = len(a)
    n = len(a[0])

    # Structural preprocessing: drop all-zero columns and all-zero rows.
    kept_cols = [j for j in range(n) if any(a[i][j] for i in range(m))]
    if len(kept_cols) < n:
        a = [[row[j] for j in kept_cols] for row in a]
        n = len(kept_cols)
    kept_rows = [i for i in range(m) if any(a[i])]
    if len(kept_rows) < m:
        if any(b[i] != 0 for i in range(m) if i not in kept_rows):
            return False
        a = [a[i] for i in kept_rows]
        b = [b[i] for i in kept_rows]
        m = len(a)
    if n == 0:
        return all(x == 0 for x in b)

    # A variable in column j cannot exceed the smallest RHS among the rows
    # it participates in; a zero RHS pins the whole column to zero.
    initial_hi: List[Optional[int]] = []
    for j in range(n):
        bounds = [b[i] for i in range(m) if a[i][j]]
        initial_hi.append(min(bounds) if bounds else None)
    initial_lo = [0] * n

    nodes = 0

    def branch(lo: List[int], hi: List[Optional[int]]) -> bool:
        nonlocal nodes
        nodes += 1
        if nodes > node_budget:
            raise FeasibilityError(
                f"integer feasibility search exceeded {node_budget} nodes"
            )

        propagated = _propagate(a, b, lo, hi)
        if propagated is None:
            return False
        lo, hi = propagated
        if all(hi[j] == lo[j] for j in range(n)):
            return all(
                sum(a[i][j] * lo[j] for j in range(n)) == b[i]
                for i in range(m)
            )

        feasible, solution = _lp_relaxation(a, b, lo, hi)
        if not feasible:
            return False
        assert solution is not None
        frac = next(
            (
                j
                for j in range(n)
                if hi[j] != lo[j] and solution[j].denominator != 1
            ),
            None,
        )
        if frac is None:
            # Every variable integral while equalities hold: the relaxation
            # itself is an integer solution.
            return True

        fv = solution[frac]
        floor_v = fv.numerator // fv.denominator
        # Branch y <= floor(fv), then y >= ceil(fv).
        hi_left = hi[:]
        hi_left[frac] = min(
            hi[frac] if hi[frac] is not None else floor_v, floor_v
        )
        if branch(lo, hi_left):
            return True
        lo_right = lo[:]
        lo_right[frac] = max(lo[frac], floor_v + 1)
        return branch(lo_right, hi)

    return branch(initial_lo, initial_hi)
