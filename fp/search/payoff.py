"""Experimental root matrix selection, using the same simulations as MCTS.

Values are heuristic search returns, not calibrated win probabilities. Every
unvisited pair uses an explicitly computed one-turn chance expectation.
"""

from dataclasses import dataclass
import math
import random


@dataclass
class MatrixSolution:
    policy: list[float]
    opponent_policy: list[float]
    value: float
    gap: float


def solve_matrix(matrix: list[list[float]]) -> MatrixSolution:
    """Solve a finite zero-sum game by primal/dual simplex (no ML dependency)."""
    n = len(matrix)
    m = len(matrix[0]) if n else 0
    if not m or any(len(row) != m for row in matrix):
        raise ValueError("Empty or ragged payoff matrix")
    if any(not math.isfinite(x) for row in matrix for x in row):
        raise ValueError("Nonfinite payoff")
    shift = 1 - min(x for row in matrix for x in row)
    # max sum(y) subject to (M + shift)y <= 1, y >= 0.
    table = [
        [x + shift for x in row] + [float(i == j) for j in range(n)] + [1.0]
        for i, row in enumerate(matrix)
    ]
    table.append([-1.0] * m + [0.0] * (n + 1))
    basis = list(range(m, m + n))
    for _ in range(10000):
        # Bland's rule avoids cycling on degenerate games.
        entering = next((j for j in range(m + n) if table[-1][j] < -1e-10), None)
        if entering is None:
            break
        eligible = [i for i in range(n) if table[i][entering] > 1e-12]
        if not eligible:
            raise ValueError("Unbounded game LP")
        leaving = min(
            eligible, key=lambda i: (table[i][-1] / table[i][entering], basis[i])
        )
        pivot = table[leaving][entering]
        table[leaving] = [v / pivot for v in table[leaving]]
        for i in range(n + 1):
            if i == leaving:
                continue
            scale = table[i][entering]
            table[i] = [a - scale * b for a, b in zip(table[i], table[leaving])]
        basis[leaving] = entering
    else:
        raise ValueError("Game LP iteration limit")
    p = [max(0.0, x) for x in table[-1][m : m + n]]
    q = [0.0] * m
    for i, variable in enumerate(basis):
        if variable < m:
            q[variable] = max(0.0, table[i][-1])
    if min(sum(p), sum(q)) <= 0:
        raise ValueError("Invalid game strategies")
    p = [x / sum(p) for x in p]
    q = [x / sum(q) for x in q]
    lower = min(sum(p[i] * matrix[i][j] for i in range(n)) for j in range(m))
    upper = max(sum(matrix[i][j] * q[j] for j in range(m)) for i in range(n))
    if upper - lower > 1e-6:
        raise ValueError("Game LP failed best-response verification")
    value = sum(p[i] * matrix[i][j] * q[j] for i in range(n) for j in range(m))
    return MatrixSolution(p, q, value, max(0.0, upper - lower))


def solve_worlds(matrices, weights):
    """One own policy across worlds; opposing responses may depend on their set.

    A constraint-generation oracle avoids enumerating the Cartesian product of
    opposing action profiles. This is a root approximation, not an infoset solver.
    """
    if (
        not matrices
        or len(matrices) != len(weights)
        or any(w <= 0 or not math.isfinite(w) for w in weights)
    ):
        raise ValueError("Invalid weighted worlds")
    n = len(matrices[0])
    if not n or any(
        len(matrix) != n
        or not matrix[0]
        or any(
            len(row) != len(matrix[0]) or any(not math.isfinite(x) for x in row)
            for row in matrix
        )
        for matrix in matrices
    ):
        raise ValueError("World action mismatch")
    weights = [w / sum(weights) for w in weights]
    # Each constraint is an achievable opposing response profile, never a
    # per-row minimum (which would let the foe see our sampled action).
    columns = [
        [
            sum(w * matrix[i][0] for matrix, w in zip(matrices, weights))
            for i in range(n)
        ]
    ]
    for _ in range(1000):
        solution = solve_matrix([list(row) for row in zip(*columns)])
        response = [
            min(
                range(len(matrix[0])),
                key=lambda j: sum(solution.policy[i] * matrix[i][j] for i in range(n)),
            )
            for matrix in matrices
        ]
        column = [
            sum(w * matrix[i][j] for matrix, w, j in zip(matrices, weights, response))
            for i in range(n)
        ]
        lower = sum(p * x for p, x in zip(solution.policy, column))
        if solution.value - lower <= 1e-6:
            solution.gap = max(solution.gap, solution.value - lower)
            return solution
        columns.append(column)
    raise ValueError("Belief game constraint limit")


def diagnostic_search(state, duration_ms, iterations=0, seed=0):
    from poke_engine import MctsResult, seeded_search

    native, priors, visits, scores = seeded_search(state, duration_ms, iterations, seed)
    result = MctsResult._from_rust(native)
    # One prior observation regularizes noisy cells. Missing cells remain
    # explicitly shallow estimates; they are never silently assigned zero loss.
    result.payoff_matrix = [
        [(scores[i][j] + prior) / (visits[i][j] + 1) for j, prior in enumerate(row)]
        for i, row in enumerate(priors)
    ]
    result.root_joint_visits = visits
    result.immediate_payoffs = priors
    return result


def matrix_policy(results):
    if not results:
        raise ValueError("No search worlds")
    rows = [o.move_choice for o in results[0][0].side_one]
    matrices, weights = [], []
    for result, weight, _ in results:
        if [o.move_choice for o in result.side_one] != rows:
            raise ValueError("Own legal actions differ across hypotheses")
        matrices.append(result.payoff_matrix)
        weights.append(weight)
    return rows, solve_worlds(matrices, weights)


def select_matrix_action(results, rng=None):
    rows, solution = matrix_policy(results)
    return (rng or random).choices(rows, weights=solution.policy)[0]
