"""Fast physics-guided stiffness screening for PortalCalc.

This module intentionally has no Qt or PortalCalc UI dependencies.  It works
with stiffness contributions, catalogue property points and linear response
constraints supplied by the desktop application.  The detailed PortalCalc
analysis remains the sole design verifier.

The screening model keeps the exact elastic member geometry of the captured
frame, but removes load generation, member checks, connection checks, costing
and reporting from each trial.  That makes it a useful first step toward a
fully condensed portal/truss macro model while remaining straightforward to
validate against the existing solver.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
import itertools
import math
from typing import Iterable, Mapping, Sequence

import numpy as np


_TINY = 1.0e-12


@dataclass(frozen=True)
class CataloguePoint:
    """One real section available to a reduced stiffness variable."""

    name: str
    area_mm2: float
    inertia_mm4: float
    cost: float
    frozen_ratio: float = 0.0


@dataclass(frozen=True)
class ResponseConstraint:
    """A linear displacement response under one captured load case."""

    name: str
    load: np.ndarray
    response: np.ndarray
    limit_mm: float
    offset_mm: float = 0.0


@dataclass(frozen=True)
class StiffnessVariable:
    """Catalogue-backed axial and flexural stiffness contribution."""

    name: str
    groups: tuple[str, ...]
    axial_matrix_per_mm2: np.ndarray
    bending_matrix_per_mm4: np.ndarray
    points: tuple[CataloguePoint, ...]
    baseline_name: str
    metric: str

    def design_metric(self, point: CataloguePoint) -> float:
        if self.metric == "area":
            return max(float(point.area_mm2), _TINY)
        return max(float(point.inertia_mm4), _TINY)

    def point(self, name: str) -> CataloguePoint:
        for point in self.points:
            if point.name == name:
                return point
        raise KeyError(f"Unknown option {name!r} for {self.name}")

    @cached_property
    def efficient_frontier(self) -> tuple[CataloguePoint, ...]:
        """Cost/stiffness Pareto frontier in stiffness order."""
        cheapest_for_metric: dict[float, CataloguePoint] = {}
        for point in self.points:
            metric = self.design_metric(point)
            previous = cheapest_for_metric.get(metric)
            if previous is None or (point.cost, point.name) < (previous.cost, previous.name):
                cheapest_for_metric[metric] = point
        ordered = sorted(
            cheapest_for_metric.values(),
            key=lambda point: (self.design_metric(point), point.cost, point.name),
        )
        kept_reversed: list[CataloguePoint] = []
        best_cost = math.inf
        for point in reversed(ordered):
            if point.cost < best_cost - 1.0e-9:
                kept_reversed.append(point)
                best_cost = point.cost
        frontier = tuple(reversed(kept_reversed))
        return frontier or tuple(ordered)

    def efficient_points(self) -> tuple[CataloguePoint, ...]:
        return self.efficient_frontier


@dataclass(frozen=True)
class ScreenEvaluation:
    target_metrics: Mapping[str, float]
    selected_sections: Mapping[str, str]
    predicted_cost: float
    constraint_ratios: Mapping[str, float]
    governing_constraint: str
    governing_ratio: float
    score: float


@dataclass(frozen=True)
class StiffnessOptimum:
    target_metrics: Mapping[str, float]
    predicted_cost: float
    constraint_ratios: Mapping[str, float]
    governing_constraint: str
    governing_ratio: float
    score: float
    evaluations: int


@dataclass(frozen=True)
class ScreeningResult:
    optimum: StiffnessOptimum
    candidates: tuple[ScreenEvaluation, ...]


def _safe_log(value: float) -> float:
    return math.log(max(float(value), _TINY))


def _log_interpolate(left: float, right: float, fraction: float) -> float:
    if left <= 0.0 or right <= 0.0:
        return left + fraction * (right - left)
    return math.exp(_safe_log(left) + fraction * (_safe_log(right) - _safe_log(left)))


def _interpolated_point(variable: StiffnessVariable, target_metric: float) -> CataloguePoint:
    points = variable.efficient_points()
    if not points:
        raise ValueError(f"{variable.name} has no catalogue points")
    target = max(float(target_metric), _TINY)
    metrics = [variable.design_metric(point) for point in points]
    if target <= metrics[0]:
        return points[0]
    if target >= metrics[-1]:
        return points[-1]
    for left, right, left_metric, right_metric in zip(points, points[1:], metrics, metrics[1:]):
        if target > right_metric:
            continue
        span = _safe_log(right_metric) - _safe_log(left_metric)
        fraction = 0.0 if abs(span) <= _TINY else (_safe_log(target) - _safe_log(left_metric)) / span
        return CataloguePoint(
            name=f"{left.name}..{right.name}",
            area_mm2=_log_interpolate(left.area_mm2, right.area_mm2, fraction),
            inertia_mm4=_log_interpolate(left.inertia_mm4, right.inertia_mm4, fraction),
            cost=left.cost + fraction * (right.cost - left.cost),
            frozen_ratio=max(left.frozen_ratio, right.frozen_ratio),
        )
    return points[-1]


class ReducedElasticScreen:
    """Small repeated elastic solve assembled from catalogue-backed variables."""

    def __init__(
        self,
        fixed_matrix: np.ndarray,
        restrained_dofs: Iterable[int],
        variables: Sequence[StiffnessVariable],
        constraints: Sequence[ResponseConstraint],
        fixed_cost: float = 0.0,
        screening_margin: float = 0.97,
    ) -> None:
        fixed = np.asarray(fixed_matrix, dtype=float)
        if fixed.ndim != 2 or fixed.shape[0] != fixed.shape[1]:
            raise ValueError("fixed_matrix must be square")
        self.fixed_matrix = fixed.copy()
        self.variables = tuple(variables)
        self.constraints = tuple(constraints)
        self.fixed_cost = float(fixed_cost)
        self.screening_margin = min(max(float(screening_margin), 0.5), 1.0)
        restrained = {int(dof) for dof in restrained_dofs}
        self.free_dofs = np.array(
            [dof for dof in range(fixed.shape[0]) if dof not in restrained],
            dtype=int,
        )
        if not len(self.free_dofs):
            raise ValueError("screening model has no free degrees of freedom")
        for variable in self.variables:
            if variable.axial_matrix_per_mm2.shape != fixed.shape:
                raise ValueError(f"axial matrix shape mismatch for {variable.name}")
            if variable.bending_matrix_per_mm4.shape != fixed.shape:
                raise ValueError(f"bending matrix shape mismatch for {variable.name}")
            if not variable.points:
                raise ValueError(f"{variable.name} has no catalogue points")
        for constraint in self.constraints:
            if np.asarray(constraint.load).shape != (fixed.shape[0],):
                raise ValueError(f"load vector shape mismatch for {constraint.name}")
            if np.asarray(constraint.response).shape != (fixed.shape[0],):
                raise ValueError(f"response vector shape mismatch for {constraint.name}")
            if constraint.limit_mm <= 0.0:
                raise ValueError(f"response limit must be positive for {constraint.name}")

    def bounds(self) -> dict[str, tuple[float, float]]:
        return {
            variable.name: (
                min(variable.design_metric(point) for point in variable.efficient_points()),
                max(variable.design_metric(point) for point in variable.efficient_points()),
            )
            for variable in self.variables
        }

    def baseline_targets(self) -> dict[str, float]:
        targets = {}
        for variable in self.variables:
            try:
                point = variable.point(variable.baseline_name)
            except KeyError:
                point = variable.efficient_points()[0]
            targets[variable.name] = variable.design_metric(point)
        return targets

    def _properties_for_targets(self, targets: Mapping[str, float]) -> dict[str, CataloguePoint]:
        return {
            variable.name: _interpolated_point(variable, targets[variable.name])
            for variable in self.variables
        }

    def stiffness_matrix(self, properties: Mapping[str, CataloguePoint]) -> np.ndarray:
        matrix = self.fixed_matrix.copy()
        for variable in self.variables:
            point = properties[variable.name]
            matrix += variable.axial_matrix_per_mm2 * point.area_mm2
            matrix += variable.bending_matrix_per_mm4 * point.inertia_mm4
        return matrix

    def evaluate_targets(self, targets: Mapping[str, float]) -> ScreenEvaluation:
        properties = self._properties_for_targets(targets)
        return self._evaluate(properties, targets=targets, selected_sections={})

    def evaluate_selection(self, selection: Mapping[str, str]) -> ScreenEvaluation:
        properties = {
            variable.name: variable.point(selection[variable.name])
            for variable in self.variables
        }
        targets = {
            variable.name: variable.design_metric(properties[variable.name])
            for variable in self.variables
        }
        return self._evaluate(properties, targets=targets, selected_sections=dict(selection))

    def _evaluate(
        self,
        properties: Mapping[str, CataloguePoint],
        *,
        targets: Mapping[str, float],
        selected_sections: Mapping[str, str],
    ) -> ScreenEvaluation:
        matrix = self.stiffness_matrix(properties)
        free = self.free_dofs
        ratios: dict[str, float] = {}
        if self.constraints:
            loads = np.column_stack([constraint.load[free] for constraint in self.constraints])
            try:
                displacements = np.linalg.solve(matrix[np.ix_(free, free)], loads)
                for index, constraint in enumerate(self.constraints):
                    value = float(constraint.response[free] @ displacements[:, index]) + constraint.offset_mm
                    ratios[constraint.name] = abs(value) / constraint.limit_mm
            except np.linalg.LinAlgError:
                ratios = {constraint.name: math.inf for constraint in self.constraints}
        governing_name, governing_ratio = max(
            ratios.items(),
            default=("No captured serviceability constraint", 0.0),
            key=lambda item: item[1],
        )
        cost = self.fixed_cost + sum(point.cost for point in properties.values())
        target_ratio = max(self.screening_margin, _TINY)
        exceedances = [max(ratio / target_ratio - 1.0, 0.0) for ratio in ratios.values()]
        penalty = 1000.0 * sum(value * value for value in exceedances)
        frozen_penalty = 1000.0 * sum(
            max(point.frozen_ratio - 1.0, 0.0) ** 2 for point in properties.values()
        )
        normalizer = max(self.fixed_cost + sum(
            variable.point(variable.baseline_name).cost
            if any(point.name == variable.baseline_name for point in variable.points)
            else min(point.cost for point in variable.points)
            for variable in self.variables
        ), 1.0)
        score = cost / normalizer + penalty + frozen_penalty
        return ScreenEvaluation(
            target_metrics=dict(targets),
            selected_sections=dict(selected_sections),
            predicted_cost=cost,
            constraint_ratios=ratios,
            governing_constraint=governing_name,
            governing_ratio=governing_ratio,
            score=score,
        )

    def optimise_continuous(self, max_iterations: int = 80) -> StiffnessOptimum:
        """Coordinate-search the relaxed catalogue frontiers in log stiffness."""
        bounds = self.bounds()
        names = [variable.name for variable in self.variables]
        if not names:
            raise ValueError("screening model has no stiffness variables")
        lower = np.array([_safe_log(bounds[name][0]) for name in names])
        upper = np.array([_safe_log(bounds[name][1]) for name in names])
        baseline = self.baseline_targets()
        baseline_log = np.array([_safe_log(baseline[name]) for name in names])
        starts = [
            np.clip(baseline_log, lower, upper),
            upper.copy(),
            lower + 0.35 * (upper - lower),
            lower + 0.50 * (upper - lower),
            lower + 0.65 * (upper - lower),
            lower + 0.80 * (upper - lower),
        ]
        left_column_index = next(
            (index for index, variable in enumerate(self.variables) if set(variable.groups) == {"LEFT_COLUMN"}),
            None,
        )
        right_column_index = next(
            (index for index, variable in enumerate(self.variables) if set(variable.groups) == {"RIGHT_COLUMN"}),
            None,
        )
        if left_column_index is not None and right_column_index is not None:
            mirrored = np.clip(baseline_log, lower, upper)
            left_fraction = (
                (baseline_log[left_column_index] - lower[left_column_index])
                / max(upper[left_column_index] - lower[left_column_index], _TINY)
            )
            right_fraction = (
                (baseline_log[right_column_index] - lower[right_column_index])
                / max(upper[right_column_index] - lower[right_column_index], _TINY)
            )
            mirrored[left_column_index] = lower[left_column_index] + right_fraction * (
                upper[left_column_index] - lower[left_column_index]
            )
            mirrored[right_column_index] = lower[right_column_index] + left_fraction * (
                upper[right_column_index] - lower[right_column_index]
            )
            starts.append(mirrored)
        evaluations = 0
        best: ScreenEvaluation | None = None
        for start in starts:
            current_log = start.copy()
            current = self.evaluate_targets(dict(zip(names, np.exp(current_log))))
            evaluations += 1
            step = 0.55
            iteration = 0
            while iteration < max_iterations and step >= 0.015:
                iteration += 1
                trial_best = current
                trial_log = current_log
                for index in range(len(names)):
                    for direction in (-1.0, 1.0):
                        proposed_log = current_log.copy()
                        proposed_log[index] = min(max(proposed_log[index] + direction * step, lower[index]), upper[index])
                        if abs(proposed_log[index] - current_log[index]) <= 1.0e-12:
                            continue
                        proposed = self.evaluate_targets(dict(zip(names, np.exp(proposed_log))))
                        evaluations += 1
                        if proposed.score < trial_best.score - 1.0e-10:
                            trial_best = proposed
                            trial_log = proposed_log
                if trial_best is current:
                    step *= 0.5
                else:
                    current = trial_best
                    current_log = trial_log
            if best is None or current.score < best.score:
                best = current
        assert best is not None
        return StiffnessOptimum(
            target_metrics=best.target_metrics,
            predicted_cost=best.predicted_cost,
            constraint_ratios=best.constraint_ratios,
            governing_constraint=best.governing_constraint,
            governing_ratio=best.governing_ratio,
            score=best.score,
            evaluations=evaluations,
        )

    @staticmethod
    def _nearby_points(variable: StiffnessVariable, target: float, count: int = 5) -> tuple[CataloguePoint, ...]:
        points = sorted(
            variable.points,
            key=lambda point: (
                abs(_safe_log(variable.design_metric(point)) - _safe_log(target)),
                point.cost,
                point.name,
            ),
        )
        nearby = list(points[:count])
        above = [point for point in variable.points if variable.design_metric(point) >= target]
        if above:
            nearby.append(min(above, key=lambda point: (point.cost, variable.design_metric(point), point.name)))
        try:
            nearby.append(variable.point(variable.baseline_name))
        except KeyError:
            pass
        unique = {point.name: point for point in nearby}
        return tuple(unique.values())

    def project_candidates(
        self,
        optimum: StiffnessOptimum,
        *,
        candidate_count: int = 8,
        beam_width: int = 48,
        neighbours_per_variable: int = 5,
    ) -> tuple[ScreenEvaluation, ...]:
        """Project a relaxed optimum to a bounded set of real catalogues."""
        variables = self.variables
        nearby = {
            variable.name: self._nearby_points(
                variable,
                optimum.target_metrics[variable.name],
                neighbours_per_variable,
            )
            for variable in variables
        }
        fallback = {
            variable.name: min(
                nearby[variable.name],
                key=lambda point: (
                    0 if variable.design_metric(point) >= optimum.target_metrics[variable.name] * (1.0 - 1e-9) else 1,
                    point.cost,
                    abs(_safe_log(variable.design_metric(point)) - _safe_log(optimum.target_metrics[variable.name])),
                ),
            ).name
            for variable in variables
        }
        partials: list[dict[str, str]] = [{}]
        for variable in variables:
            expanded: list[tuple[float, dict[str, str]]] = []
            for partial, point in itertools.product(partials, nearby[variable.name]):
                candidate = dict(partial)
                candidate[variable.name] = point.name
                completed = dict(fallback)
                completed.update(candidate)
                expanded.append((self.evaluate_selection(completed).score, candidate))
            expanded.sort(key=lambda item: (item[0], tuple(sorted(item[1].items()))))
            partials = [item[1] for item in expanded[:beam_width]]
        evaluated: dict[tuple[tuple[str, str], ...], ScreenEvaluation] = {}
        for selection in partials:
            key = tuple(sorted(selection.items()))
            evaluated[key] = self.evaluate_selection(selection)
        baseline_selection = {
            variable.name: variable.baseline_name for variable in variables
        }
        baseline_key = tuple(sorted(baseline_selection.items()))
        evaluated[baseline_key] = self.evaluate_selection(baseline_selection)
        individual_economic: list[dict[str, str]] = []
        economic_step = {}
        for variable in variables:
            current = variable.point(fallback[variable.name])
            cheaper = [
                point for point in variable.points
                if point.cost < current.cost - 1e-9
                and point.frozen_ratio <= 1.15
                and variable.design_metric(point) < variable.design_metric(current) - 1e-9
            ]
            if cheaper:
                economic_step[variable.name] = min(
                    cheaper,
                    key=lambda point: (
                        abs(_safe_log(variable.design_metric(point)) - _safe_log(variable.design_metric(current))),
                        -point.cost,
                        point.name,
                    ),
                ).name
                selection = dict(fallback)
                selection[variable.name] = economic_step[variable.name]
                individual_economic.append(selection)
        end_columns = [
            variable.name for variable in variables
            if set(variable.groups) in ({"LEFT_COLUMN"}, {"RIGHT_COLUMN"})
            and variable.name in economic_step
        ]
        column_selection = dict(fallback)
        if len(end_columns) == 2:
            for name in end_columns:
                column_selection[name] = economic_step[name]
        column_by_group = {
            variable.groups[0]: variable
            for variable in variables
            if len(variable.groups) == 1 and variable.groups[0] in {"LEFT_COLUMN", "RIGHT_COLUMN"}
        }
        mirrored_column_selection = dict(fallback)
        if set(column_by_group) == {"LEFT_COLUMN", "RIGHT_COLUMN"}:
            left_variable = column_by_group["LEFT_COLUMN"]
            right_variable = column_by_group["RIGHT_COLUMN"]
            for variable, opposite in (
                (left_variable, right_variable),
                (right_variable, left_variable),
            ):
                target = optimum.target_metrics[opposite.name]
                eligible = [point for point in variable.points if point.frozen_ratio <= 1.15]
                if eligible:
                    mirrored_column_selection[variable.name] = min(
                        eligible,
                        key=lambda point: (
                            abs(_safe_log(variable.design_metric(point)) - _safe_log(target)),
                            point.cost,
                            point.name,
                        ),
                    ).name
        flexible_step = {}
        for variable in variables:
            if not any(" @ " in point.name for point in variable.points):
                continue
            flexibility_target = optimum.target_metrics[variable.name] * 0.75
            eligible = [point for point in variable.points if point.frozen_ratio <= 1.15]
            if eligible:
                band = [
                    point for point in eligible
                    if flexibility_target * 0.88
                    <= variable.design_metric(point)
                    <= flexibility_target * 1.12
                ]
                flexible_step[variable.name] = min(
                    band or eligible,
                    key=lambda point: (
                        point.cost if band else abs(
                            _safe_log(variable.design_metric(point)) - _safe_log(flexibility_target)
                        ),
                        abs(_safe_log(variable.design_metric(point)) - _safe_log(flexibility_target)),
                        point.name,
                    ),
                ).name
        flexible_selection = dict(fallback)
        flexible_selection.update(flexible_step)
        coupled_selection = dict(column_selection)
        coupled_selection.update(flexible_step)
        mirrored_flexible_selection = dict(mirrored_column_selection)
        mirrored_flexible_selection.update(flexible_step)

        transfer_selections = []
        if set(column_by_group) == {"LEFT_COLUMN", "RIGHT_COLUMN"}:
            left_variable = column_by_group["LEFT_COLUMN"]
            right_variable = column_by_group["RIGHT_COLUMN"]

            def sampled_column_points(variable, limit=20):
                eligible = sorted(
                    (point for point in variable.points if point.frozen_ratio <= 1.15),
                    key=lambda point: (
                        variable.design_metric(point), point.cost, point.name,
                    ),
                )
                if len(eligible) <= limit:
                    return eligible
                indices = {
                    int(round(index * (len(eligible) - 1) / (limit - 1)))
                    for index in range(limit)
                }
                cheapest = min(range(len(eligible)), key=lambda index: eligible[index].cost)
                indices.add(cheapest)
                return [eligible[index] for index in sorted(indices)]

            left_points = sampled_column_points(left_variable)
            right_points = sampled_column_points(right_variable)
            transfer_results = []
            for macro_selection in (fallback, flexible_selection):
                for left_point, right_point in itertools.product(left_points, right_points):
                    selection = dict(macro_selection)
                    selection[left_variable.name] = left_point.name
                    selection[right_variable.name] = right_point.name
                    result = self.evaluate_selection(selection)
                    evaluated[tuple(sorted(selection.items()))] = result
                    transfer_results.append((result, left_point, right_point))

            def transfer_key(item):
                result, _, _ = item
                return (
                    0 if result.governing_ratio <= 1.05 else 1,
                    result.score,
                    result.predicted_cost,
                )

            transfer_buckets = [
                transfer_results,
                [item for item in transfer_results if (
                    right_variable.design_metric(item[2])
                    > left_variable.design_metric(item[1]) * 1.35
                )],
                [item for item in transfer_results if (
                    left_variable.design_metric(item[1])
                    > right_variable.design_metric(item[2]) * 1.35
                )],
                sorted(
                    (item for item in transfer_results if item[0].governing_ratio <= 1.12),
                    key=lambda item: (item[0].predicted_cost, item[0].score),
                ),
            ]
            # Asymmetric frames can be cheapest well away from a balanced
            # column pair.  Keep explicit, economical transfer hypotheses in
            # both directions instead of allowing the global score to pull
            # every reserved candidate back toward equal stiffness.
            for direction in (1, -1):
                extreme = []
                for item in transfer_results:
                    _, left_point, right_point = item
                    left_metric = left_variable.design_metric(left_point)
                    right_metric = right_variable.design_metric(right_point)
                    if direction > 0 and right_metric > left_metric * 2.25:
                        extreme.append(item)
                    elif direction < 0 and left_metric > right_metric * 2.25:
                        extreme.append(item)
                transfer_buckets.append(sorted(
                    extreme,
                    key=lambda item: (
                        0 if item[0].governing_ratio <= 1.30 else 1,
                        item[0].predicted_cost,
                        item[0].score,
                    ),
                ))
                # Preserve several points along the asymmetric cost/response
                # frontier.  A single cheapest point is often too flexible,
                # while the single best-response point can be needlessly
                # heavy once the detailed truss is restored.
                for ratio_limit in (1.20, 1.10, 1.02):
                    transfer_buckets.append(sorted(
                        (item for item in extreme if item[0].governing_ratio <= ratio_limit),
                        key=lambda item: (item[0].predicted_cost, item[0].score),
                    ))
            transfer_seen = set()
            economic_bucket_start = 3
            for bucket_index, bucket in enumerate(transfer_buckets):
                if not bucket:
                    continue
                item = bucket[0] if bucket_index >= economic_bucket_start else min(bucket, key=transfer_key)
                selection = dict(item[0].selected_sections)
                key = tuple(sorted(selection.items()))
                if key not in transfer_seen:
                    transfer_seen.add(key)
                    transfer_selections.append(selection)

        exploration_selections = []
        exploration_selections.extend(transfer_selections)
        if mirrored_column_selection != fallback:
            exploration_selections.append(mirrored_column_selection)
        if mirrored_flexible_selection != fallback:
            exploration_selections.append(mirrored_flexible_selection)
        if len(end_columns) == 2:
            exploration_selections.append(column_selection)
        if flexible_step:
            exploration_selections.append(flexible_selection)
        if len(end_columns) == 2 and flexible_step:
            exploration_selections.append(coupled_selection)
        exploration_selections.extend(individual_economic)
        exploration = []
        for selection in exploration_selections:
            key = tuple(sorted(selection.items()))
            result = self.evaluate_selection(selection)
            evaluated[key] = result
            exploration.append(result)
        ranked = sorted(
            evaluated.values(),
            key=lambda result: (
                0 if result.governing_ratio <= self.screening_margin else 1,
                result.score,
                result.predicted_cost,
                tuple(sorted(result.selected_sections.items())),
            ),
        )
        count = max(int(candidate_count), 1)
        reserved = []
        seen_reserved = set()
        for result in exploration:
            key = tuple(sorted(result.selected_sections.items()))
            if key not in seen_reserved:
                seen_reserved.add(key)
                reserved.append(result)
            if len(reserved) >= min(14, count):
                break
        selected = []
        reserved_keys = {tuple(sorted(result.selected_sections.items())) for result in reserved}
        for result in ranked:
            key = tuple(sorted(result.selected_sections.items()))
            if key in reserved_keys:
                continue
            selected.append(result)
            if len(selected) >= count - len(reserved):
                break
        selected.extend(reserved)
        return tuple(selected[:count])

    def run(self, candidate_count: int = 8) -> ScreeningResult:
        optimum = self.optimise_continuous()
        candidates = self.project_candidates(optimum, candidate_count=candidate_count)
        return ScreeningResult(optimum=optimum, candidates=candidates)

    def to_response_basis(self, max_modes: int = 96) -> "ReducedElasticScreen":
        """Project the fine elastic model onto load, adjoint and sensitivity modes.

        Baseline load responses are included explicitly, followed by response
        adjoints and first-order section-stiffness response directions.  This
        retains the physics relevant to the captured constraints while making
        repeated catalogue screening independent of the truss panel DOF count.
        """
        if not self.constraints:
            return self
        properties = self._properties_for_targets(self.baseline_targets())
        stiffness = self.stiffness_matrix(properties)
        free = self.free_dofs
        k_ff = stiffness[np.ix_(free, free)]
        loads = np.column_stack([constraint.load[free] for constraint in self.constraints])
        responses = np.column_stack([constraint.response[free] for constraint in self.constraints])
        try:
            baseline_displacements = np.linalg.solve(k_ff, loads)
            adjoints = np.linalg.solve(k_ff, responses)
            sensitivity_rhs = []
            for variable in self.variables:
                point = properties[variable.name]
                variable_matrix = (
                    variable.axial_matrix_per_mm2 * point.area_mm2
                    + variable.bending_matrix_per_mm4 * point.inertia_mm4
                )
                sensitivity_rhs.append(variable_matrix[np.ix_(free, free)] @ baseline_displacements)
            sensitivities = np.linalg.solve(
                k_ff,
                np.column_stack(sensitivity_rhs),
            ) if sensitivity_rhs else np.empty((len(free), 0))
        except np.linalg.LinAlgError:
            return self
        snapshots = np.column_stack([baseline_displacements, adjoints, sensitivities])
        column_norms = np.linalg.norm(snapshots, axis=0)
        snapshots = snapshots[:, column_norms > max(float(column_norms.max(initial=0.0)) * 1e-12, _TINY)]
        if not snapshots.shape[1]:
            return self
        basis, triangular = np.linalg.qr(snapshots, mode="reduced")
        diagonal = np.abs(np.diag(triangular))
        rank = int(np.count_nonzero(diagonal > max(diagonal.max(initial=0.0) * 1e-10, _TINY)))
        rank = min(max(rank, 1), int(max_modes), basis.shape[1])
        basis = basis[:, :rank]

        def project(matrix: np.ndarray) -> np.ndarray:
            return basis.T @ matrix[np.ix_(free, free)] @ basis

        projected_variables = tuple(StiffnessVariable(
            name=variable.name,
            groups=variable.groups,
            axial_matrix_per_mm2=project(variable.axial_matrix_per_mm2),
            bending_matrix_per_mm4=project(variable.bending_matrix_per_mm4),
            points=variable.points,
            baseline_name=variable.baseline_name,
            metric=variable.metric,
        ) for variable in self.variables)
        projected_constraints = tuple(ResponseConstraint(
            name=constraint.name,
            load=basis.T @ constraint.load[free],
            response=basis.T @ constraint.response[free],
            limit_mm=constraint.limit_mm,
            offset_mm=constraint.offset_mm,
        ) for constraint in self.constraints)
        reduced = ReducedElasticScreen(
            fixed_matrix=project(self.fixed_matrix),
            restrained_dofs=(),
            variables=projected_variables,
            constraints=projected_constraints,
            fixed_cost=self.fixed_cost,
            screening_margin=self.screening_margin,
        )
        reduced.full_order_dofs = len(free)
        reduced.response_basis_modes = rank
        return reduced


def statically_condense(
    stiffness: np.ndarray,
    loads: np.ndarray,
    retained_dofs: Sequence[int],
) -> tuple[np.ndarray, np.ndarray]:
    """Return exact static stiffness/load condensation onto retained DOFs.

    This is exposed separately for the next macro-element step and for direct
    regression tests.  ``loads`` may be one vector or a matrix of load cases.
    """
    matrix = np.asarray(stiffness, dtype=float)
    force = np.asarray(loads, dtype=float)
    vector_input = force.ndim == 1
    if vector_input:
        force = force[:, None]
    retained = np.array(sorted({int(dof) for dof in retained_dofs}), dtype=int)
    if not len(retained):
        raise ValueError("at least one retained degree of freedom is required")
    all_dofs = np.arange(matrix.shape[0], dtype=int)
    internal = np.setdiff1d(all_dofs, retained, assume_unique=True)
    if not len(internal):
        return matrix.copy(), force[:, 0].copy() if vector_input else force.copy()
    k_rr = matrix[np.ix_(retained, retained)]
    k_ri = matrix[np.ix_(retained, internal)]
    k_ir = matrix[np.ix_(internal, retained)]
    k_ii = matrix[np.ix_(internal, internal)]
    f_r = force[retained, :]
    f_i = force[internal, :]
    solved_k = np.linalg.solve(k_ii, k_ir)
    solved_f = np.linalg.solve(k_ii, f_i)
    condensed_k = k_rr - k_ri @ solved_k
    condensed_f = f_r - k_ri @ solved_f
    return condensed_k, condensed_f[:, 0] if vector_input else condensed_f


def frame_element_property_matrices(element) -> tuple[np.ndarray, np.ndarray]:
    """Return 6x6 global stiffness matrices per unit area and inertia.

    ``element`` is intentionally duck-typed so the numerical module remains
    independent from PortalCalc's UI-owned ``FrameElement2D`` class.
    """
    length = float(element.length())
    if length <= _TINY:
        raise ValueError("frame element length must be positive")
    elastic_modulus = float(element.E)
    inertia_factor = max(float(getattr(element, "analysis_i_factor", 1.0)), _TINY)
    axial_local = np.zeros((6, 6), dtype=float)
    axial = elastic_modulus / length
    axial_local[0, 0] = axial
    axial_local[0, 3] = -axial
    axial_local[3, 0] = -axial
    axial_local[3, 3] = axial

    bending_local = np.zeros((6, 6), dtype=float)
    c1 = 12.0 * elastic_modulus * inertia_factor / length**3
    c2 = 6.0 * elastic_modulus * inertia_factor / length**2
    c3 = 4.0 * elastic_modulus * inertia_factor / length
    c4 = 2.0 * elastic_modulus * inertia_factor / length
    bending_local[np.ix_([1, 2, 4, 5], [1, 2, 4, 5])] = np.array([
        [c1, c2, -c1, c2],
        [c2, c3, -c2, c4],
        [-c1, -c2, c1, -c2],
        [c2, c4, -c2, c3],
    ])
    transform = np.asarray(element.transformation_matrix(), dtype=float)
    return transform.T @ axial_local @ transform, transform.T @ bending_local @ transform


def assemble_element_property_matrices(
    total_dofs: int,
    elements: Iterable,
) -> tuple[np.ndarray, np.ndarray]:
    """Assemble global stiffness matrices per unit A and I for like sections."""
    axial = np.zeros((int(total_dofs), int(total_dofs)), dtype=float)
    bending = np.zeros_like(axial)
    for element in elements:
        element_axial, element_bending = frame_element_property_matrices(element)
        dofs = list(element.dof_indices())
        axial[np.ix_(dofs, dofs)] += element_axial
        bending[np.ix_(dofs, dofs)] += element_bending
    return axial, bending


def build_screen_from_portalcalc(app) -> tuple[ReducedElasticScreen, dict, dict]:
    """Capture a reduced elastic screen from a solved PortalCalc window.

    The argument is duck-typed to avoid importing the Qt-owned main module.
    Returned bindings map reduced variable names back to catalogue combo-box
    indices for exact verification by the caller.
    """
    structure = getattr(app, "structure", None)
    active_specs = app.active_auto_size_group_specs()
    if structure is None or not active_specs:
        raise ValueError("Generate and solve a frame before running Physics Optimise.")
    total_dofs = len(structure.nodes) * 3
    baseline_matrix = structure.elastic_stiffness_matrix()
    fixed_matrix = baseline_matrix.copy()
    web_groups = {"WEB", "LEFT_CANOPY_WEB", "RIGHT_CANOPY_WEB"}
    chord_groups = {
        "TOP", "BOTTOM", "LEFT_CANOPY_TOP", "LEFT_CANOPY_BOTTOM",
        "RIGHT_CANOPY_TOP", "RIGHT_CANOPY_BOTTOM",
    }
    decisions: dict[int, dict] = {}
    for spec in active_specs:
        if spec[2] in web_groups:
            continue
        decisions.setdefault(id(spec[1]), {"combo": spec[1], "specs": []})["specs"].append(spec)

    variables: list[StiffnessVariable] = []
    bindings: dict[str, dict] = {}
    ratio_cache: dict[tuple, float] = {}
    for decision in decisions.values():
        combo = decision["combo"]
        groups = tuple(dict.fromkeys(spec[2] for spec in decision["specs"]))
        elements = [element for element in structure.elements if element.group in groups]
        baseline_profile = app.selected_profile(combo)
        if not elements or baseline_profile is None:
            continue
        axial_matrix, bending_matrix = assemble_element_property_matrices(total_dofs, elements)
        fixed_matrix -= axial_matrix * baseline_profile.A
        fixed_matrix -= bending_matrix * baseline_profile.I_analysis
        length_m = sum(element.length() for element in elements) / 1000.0
        points: list[CataloguePoint] = []
        point_indices: dict[str, int] = {}
        point_depths: dict[str, float] = {}
        depth_input = None
        web_element_groups: set[str] = set()
        web_combo = None
        depth_bounds = None
        if app.frame_system_combo.currentText() == "Truss" and set(groups) == {"TOP", "BOTTOM"}:
            depth_input = app.depth_input
            depth_bounds = (float(app.truss_depth_from_span()), float(app.max_main_truss_depth_mm()))
            web_element_groups = {"WEB", "POST", "APEX"}
            web_combo = app.web_profile_combo
        elif set(groups) == {"LEFT_CANOPY_TOP", "LEFT_CANOPY_BOTTOM"}:
            depth_input = app.left_canopy_shallow_depth_input
            depth_bounds = app.addition_depth_bounds("left")
            web_element_groups = {"LEFT_CANOPY_WEB", "LEFT_CANOPY_POST"}
            web_combo = app.left_canopy_web_profile_combo
        elif set(groups) == {"RIGHT_CANOPY_TOP", "RIGHT_CANOPY_BOTTOM"}:
            depth_input = app.right_canopy_shallow_depth_input
            depth_bounds = app.addition_depth_bounds("right")
            web_element_groups = {"RIGHT_CANOPY_WEB", "RIGHT_CANOPY_POST"}
            web_combo = app.right_canopy_web_profile_combo
        has_depth_variable = depth_input is not None and depth_bounds is not None
        baseline_depth = 0.0
        depth_options = (None,)
        web_depth_costs = {None: 0.0}
        if has_depth_variable:
            baseline_depth = max(float(depth_input.text()), _TINY)
            minimum_depth, maximum_depth = map(float, depth_bounds)
            first_depth = math.ceil(minimum_depth / 50.0) * 50.0
            last_depth = math.floor(maximum_depth / 50.0) * 50.0
            if last_depth < first_depth:
                raise ValueError(
                    "The truss-depth maximum is below the minimum 50 mm selectable depth."
                )
            depth_options = tuple(
                float(depth) for depth in range(
                    int(round(first_depth)), int(round(last_depth)) + 1, 50
                )
            )
            # The caller normalises the starting value before capture.  Do not
            # add an out-of-range baseline here: it would bypass a user-entered
            # maximum and let the exact verification re-adopt that depth.
            if baseline_depth not in depth_options:
                raise ValueError(
                    f"The current truss depth ({baseline_depth:.0f} mm) is outside "
                    f"the optimiser range {first_depth:.0f}–{last_depth:.0f} mm."
                )
            web_elements = [
                element for element in structure.elements
                if element.group in web_element_groups
            ]
            web_profile = app.selected_profile(web_combo)
            web_rate = float(app.auto_size_profile_cost_key(web_profile)) if web_profile is not None else 0.0
            for candidate_depth in depth_options:
                scale = candidate_depth / baseline_depth
                estimated_length = sum(
                    math.hypot(
                        element.end.x - element.start.x,
                        (element.end.y - element.start.y) * scale,
                    )
                    for element in web_elements
                ) / 1000.0
                baseline_length = sum(element.length() for element in web_elements) / 1000.0
                web_depth_costs[candidate_depth] = (estimated_length - baseline_length) * web_rate
        for index in app.auto_size_candidate_indices(combo, groups[0], min_metric_step=0.0):
            profile = app.catalog.get(combo.itemText(index))
            if profile is None:
                continue
            worst_ratio = 0.0
            for _, _, group in decision["specs"]:
                envelope = (app.design_envelope_actions or {}).get(group)
                if not app.member_envelope_has_demands(envelope):
                    continue
                cache_key = (group, profile.name, id(envelope))
                if cache_key not in ratio_cache:
                    try:
                        ratio_cache[cache_key] = app.member_and_connection_ratio(profile, envelope, group)[0]
                        if group in chord_groups:
                            single_envelope = (
                                (app.design_envelope_actions or {}).get("single_chord_envelopes") or {}
                            ).get(group)
                            if app.member_envelope_has_demands(single_envelope):
                                ratio_cache[cache_key] = max(
                                    ratio_cache[cache_key],
                                    app.member_check_result(profile, single_envelope, group).governing_ratio,
                                )
                            ratio_cache[cache_key] = max(
                                ratio_cache[cache_key],
                                app.auto_size_chord_joint_ratio(profile, group),
                            )
                    except (ValueError, ZeroDivisionError, OverflowError):
                        ratio_cache[cache_key] = math.inf
                worst_ratio = max(worst_ratio, ratio_cache[cache_key])
            for candidate_depth in depth_options:
                if candidate_depth is None:
                    point_name = profile.name
                    depth_scale = 1.0
                else:
                    point_name = f"{profile.name} @ {candidate_depth:.0f} mm"
                    depth_scale = candidate_depth / baseline_depth
                    point_depths[point_name] = candidate_depth
                points.append(CataloguePoint(
                    name=point_name,
                    # The chord couple contribution to global EI is
                    # proportional to chord area times separation squared.
                    area_mm2=max(float(profile.A) * depth_scale**2, _TINY),
                    inertia_mm4=max(float(profile.I_analysis), _TINY),
                    cost=max(
                        float(app.auto_size_search_cost_key(profile, groups[0])) * length_m
                        + web_depth_costs[candidate_depth],
                        0.0,
                    ),
                    # First-order chord force falls approximately inversely
                    # with truss depth. Exact analysis verifies the projection.
                    frozen_ratio=worst_ratio / max(depth_scale, _TINY),
                ))
                point_indices[point_name] = index
        if not points:
            continue
        name = "+".join(groups)
        baseline_name = (
            f"{baseline_profile.name} @ {baseline_depth:.0f} mm"
            if has_depth_variable else baseline_profile.name
        )
        variables.append(StiffnessVariable(
            name=name,
            groups=groups,
            axial_matrix_per_mm2=axial_matrix,
            bending_matrix_per_mm4=bending_matrix,
            points=tuple(points),
            baseline_name=baseline_name,
            metric="area" if app.frame_system_combo.currentText() == "Truss" and any(
                group in chord_groups for group in groups
            ) else "inertia",
        ))
        bindings[name] = {
            "combo": combo,
            "indices": point_indices,
            "depths": point_depths,
            "depth_input": depth_input,
            "groups": groups,
        }
    if not variables:
        raise ValueError("No stiffness-active catalogue groups were found.")

    nodes = {str(node.id): node for node in structure.nodes}
    elements = {str(element.id): element for element in structure.elements}
    constraints: list[ResponseConstraint] = []
    used_names: set[str] = set()

    def node_response(node_id, component: str):
        node = nodes.get(str(node_id))
        if node is None:
            return None
        response = np.zeros(total_dofs)
        response[node.ux if component == "ux" else node.uy] = 1.0
        return response

    def add(name: str, item: Mapping, response, limit_mm: float):
        if response is None or not isinstance(item, Mapping) or limit_mm <= _TINY:
            return
        raw_displacement = item.get("displacements")
        if raw_displacement is None:
            return
        displacement = np.asarray(raw_displacement, dtype=float)
        if displacement.shape != (total_dofs,):
            return
        unique = name
        suffix = 2
        while unique in used_names:
            unique = f"{name} {suffix}"
            suffix += 1
        used_names.add(unique)
        measured = float(response @ displacement)
        signed = float(item.get("signed_value", measured))
        constraints.append(ResponseConstraint(
            name=unique,
            load=baseline_matrix @ displacement,
            response=response,
            limit_mm=float(limit_mm),
            offset_mm=signed - measured,
        ))

    envelope = app.design_envelope_actions or {}
    divisor = float(app.eave_deflection_divisor())
    for group, item in (envelope.get("sls_column_top_deflections") or {}).items():
        height = float(item.get("height_mm") or app.serviceability_height_mm or 0.0)
        add(f"{group} top sway", item, node_response(item.get("node"), "ux"), height / divisor)

    item = envelope.get("sls_crane_rail_deflection") or {}
    height = float(item.get("height_mm") or 0.0)
    divisor = float(app.serviceability_limit_value("crane_rail_height"))
    add("Crane rail sway", item, node_response(item.get("node"), "ux"), height / divisor)

    divisor = float(app.column_deflection_divisor())
    for group, item in (envelope.get("sls_column_midspan_ws_deflections") or {}).items():
        element = elements.get(str(item.get("member")))
        height = float(item.get("height_mm") or 0.0)
        if element is None or height <= _TINY:
            continue
        group_elements = [candidate for candidate in structure.elements if candidate.group == group]
        group_nodes = [node for candidate in group_elements for node in (candidate.start, candidate.end)]
        if not group_nodes:
            continue
        target_y = min(node.y for node in group_nodes) + 0.5 * height
        dy = element.end.y - element.start.y
        station = (target_y - element.start.y) / dy if abs(dy) > _TINY else 0.5
        station = min(max(station, 0.0), 1.0)
        response = np.zeros(total_dofs)
        for dof in element.dof_indices():
            basis = np.zeros(total_dofs)
            basis[dof] = 1.0
            response[dof] = element.global_displacement_at(basis, station, (0.0, 0.0))[0]
        add(f"{group} midheight sway", item, response, height / divisor)

    span = float(app.serviceability_span_mm or 0.0)
    midspan_divisors = app.midspan_component_deflection_divisors()
    components = tuple(app.governing_midspan_deflection_components())
    for component in components:
        item = (envelope.get("sls_truss_midspan_deflections") or {}).get(component, {})
        divisor = float(midspan_divisors.get(component) or 0.0)
        add(
            f"Main span {component} deflection", item,
            node_response(item.get("node"), "uy"),
            span / divisor if divisor > 0.0 else 0.0,
        )

    for key, result in (envelope.get("sls_side_midspan_deflections") or {}).items():
        side_span = float(result.get("length_mm") or 0.0)
        for component in components:
            item = (result.get("components") or {}).get(component, {})
            node = nodes.get(str(item.get("node")))
            start = nodes.get(str(item.get("start")))
            end = nodes.get(str(item.get("end")))
            if node is None or start is None or end is None:
                continue
            dx, dy = end.x - start.x, end.y - start.y
            length_squared = dx * dx + dy * dy
            station = 0.5 if length_squared <= _TINY else (
                (node.x - start.x) * dx + (node.y - start.y) * dy
            ) / length_squared
            station = min(max(station, 0.0), 1.0)
            response = np.zeros(total_dofs)
            response[node.uy] = 1.0
            response[start.uy] -= 1.0 - station
            response[end.uy] -= station
            divisor = float(midspan_divisors.get(component) or 0.0)
            add(f"{key} {component} deflection", item, response, side_span / divisor)

    divisor = float(app.canopy_tip_deflection_divisor())
    for key, item in (envelope.get("sls_canopy_tip_deflections") or {}).items():
        tip = nodes.get(str(item.get("node")))
        anchor = nodes.get(str(item.get("anchor")))
        if tip is None or anchor is None:
            continue
        response = np.zeros(total_dofs)
        response[tip.uy] = 1.0
        response[anchor.uy] -= 1.0
        reference = nodes.get(str(item.get("tilt_reference")))
        if reference is not None and abs(anchor.y - reference.y) > _TINY:
            arm = (tip.x - anchor.x) / (anchor.y - reference.y)
            response[anchor.ux] += arm
            response[reference.ux] -= arm
        add(f"{key} tip deflection", item, response, float(item.get("length_mm") or 0.0) / divisor)

    full_screen = ReducedElasticScreen(
        fixed_matrix=fixed_matrix,
        restrained_dofs=structure.restrained_dofs,
        variables=variables,
        constraints=constraints,
        screening_margin=0.97,
    )
    rebuilt = full_screen.stiffness_matrix({
        variable.name: variable.point(variable.baseline_name) for variable in variables
    })
    error = float(np.linalg.norm(rebuilt - baseline_matrix) / max(np.linalg.norm(baseline_matrix), _TINY))
    if error > 1.0e-8:
        raise ValueError(f"Reduced stiffness decomposition did not reproduce the baseline ({error:.2e}).")
    screen = full_screen.to_response_basis()
    return screen, bindings, {
        "variables": len(variables),
        "constraints": len(constraints),
        "baseline_matrix_error": error,
        "full_free_dofs": len(full_screen.free_dofs),
        "reduced_dofs": len(screen.free_dofs),
        "second_order_screened_as_first_order": bool(app.second_order_checkbox.isChecked()),
    }
