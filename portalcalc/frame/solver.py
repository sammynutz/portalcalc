"""2D frame analysis: nodes, beam-column elements and the direct-stiffness solver."""

import math

import numpy as np


# ==========================================================
# NODE
# ==========================================================

class Node:

    def __init__(self, node_id, x, y, ux=None, uy=None, rz=None):
        self.id = node_id
        self.x = x
        self.y = y

        self.native_ux = 3 * (node_id - 1)
        self.native_uy = 3 * (node_id - 1) + 1
        self.native_rz = 3 * (node_id - 1) + 2
        self.ux = self.native_ux if ux is None else ux
        self.uy = self.native_uy if uy is None else uy
        self.rz = self.native_rz if rz is None else rz

    @property
    def unused_native_dofs(self):
        assigned = {self.ux, self.uy, self.rz}
        return [dof for dof in [self.native_ux, self.native_uy, self.native_rz] if dof not in assigned]


# ==========================================================
# 2D FRAME ELEMENT
# ==========================================================

class FrameElement2D:

    def __init__(self, element_id, start_node, end_node, E, profile, group="FRAME", analysis_i_factor=1.0):
        self.id = element_id
        self.start = start_node
        self.end = end_node
        self.E = E
        self.profile = profile
        self.A = profile.A
        self.analysis_i_factor = max(float(analysis_i_factor), 1e-9)
        self.I = profile.I_analysis * self.analysis_i_factor
        self.group = group

    def length(self):
        dx = self.end.x - self.start.x
        dy = self.end.y - self.start.y
        return math.sqrt(dx**2 + dy**2)

    def angle(self):
        dx = self.end.x - self.start.x
        dy = self.end.y - self.start.y
        return math.atan2(dy, dx)

    def _cached_matrices(self):
        """(T, k_local), rebuilt only when geometry or stiffness changes.

        Post-processing evaluates every element for every load combination
        and trial design; rebuilding these from Python lists each time was a
        large share of a 2D solve. The arrays are read-only because they are
        shared between calls.
        """
        key = (self.start.x, self.start.y, self.end.x, self.end.y, self.E, self.A, self.I)
        cached = self.__dict__.get("_matrix_cache")
        if cached is None or cached[0] != key:
            transform = self._build_transformation_matrix()
            stiffness = self._build_local_stiffness()
            transform.setflags(write=False)
            stiffness.setflags(write=False)
            cached = self._matrix_cache = (key, transform, stiffness)
        return cached[1], cached[2]

    def transformation_matrix(self):
        return self._cached_matrices()[0]

    def local_stiffness(self):
        return self._cached_matrices()[1]

    def _build_transformation_matrix(self):
        theta = self.angle()
        c = math.cos(theta)
        s = math.sin(theta)

        return np.array([
            [ c, s, 0, 0, 0, 0],
            [-s, c, 0, 0, 0, 0],
            [ 0, 0, 1, 0, 0, 0],
            [ 0, 0, 0, c, s, 0],
            [ 0, 0, 0,-s, c, 0],
            [ 0, 0, 0, 0, 0, 1]
        ])

    def _build_local_stiffness(self):
        L = self.length()
        E = self.E
        A = self.A
        I = self.I

        return np.array([
            [ A*E/L,           0,              0, -A*E/L,           0,              0],
            [ 0,      12*E*I/L**3,   6*E*I/L**2,       0, -12*E*I/L**3,   6*E*I/L**2],
            [ 0,       6*E*I/L**2,     4*E*I/L,       0,  -6*E*I/L**2,     2*E*I/L],
            [-A*E/L,           0,              0,  A*E/L,           0,              0],
            [ 0,     -12*E*I/L**3,  -6*E*I/L**2,       0,  12*E*I/L**3,  -6*E*I/L**2],
            [ 0,       6*E*I/L**2,     2*E*I/L,       0,  -6*E*I/L**2,     4*E*I/L]
        ])

    def global_stiffness(self):
        T = self.transformation_matrix()
        return T.T @ self.local_stiffness() @ T

    def local_geometric_stiffness(self, axial_tension_n):
        L = self.length()
        if L <= 1e-9 or abs(axial_tension_n) <= 1e-9:
            return np.zeros((6, 6))
        n = axial_tension_n / (30.0 * L)
        return n * np.array([
            [0, 0, 0, 0, 0, 0],
            [0, 36, 3 * L, 0, -36, 3 * L],
            [0, 3 * L, 4 * L**2, 0, -3 * L, -L**2],
            [0, 0, 0, 0, 0, 0],
            [0, -36, -3 * L, 0, 36, -3 * L],
            [0, 3 * L, -L**2, 0, -3 * L, 4 * L**2],
        ])

    def global_geometric_stiffness(self, axial_tension_n):
        T = self.transformation_matrix()
        return T.T @ self.local_geometric_stiffness(axial_tension_n) @ T

    def dof_indices(self):
        return [
            self.start.ux,
            self.start.uy,
            self.start.rz,
            self.end.ux,
            self.end.uy,
            self.end.rz
        ]

    def local_displacements(self, global_displacements):
        dofs = self.dof_indices()
        d_global = global_displacements[dofs]
        return self.transformation_matrix() @ d_global

    @staticmethod
    def load_components(local_load):
        """Legacy (qx,qy) or linear (qx_start,qy_start,qx_end,qy_end), N/mm."""
        values = tuple(local_load or (0.0, 0.0))
        if len(values) == 2:
            return values + values
        if len(values) == 4:
            return values
        raise ValueError("Element line load must have two or four components.")

    def equivalent_nodal_load_local(self, uniform_local_load=None):
        """Exact consistent nodal forces/couples for a linear element load."""
        qx1, qy1, qx2, qy2 = self.load_components(uniform_local_load)
        L = self.length()
        return np.array([
            L * (2.0 * qx1 + qx2) / 6.0,
            L * (7.0 * qy1 + 3.0 * qy2) / 20.0,
            L**2 * (3.0 * qy1 + 2.0 * qy2) / 60.0,
            L * (qx1 + 2.0 * qx2) / 6.0,
            L * (3.0 * qy1 + 7.0 * qy2) / 20.0,
            -L**2 * (2.0 * qy1 + 3.0 * qy2) / 60.0,
        ])

    def local_end_forces(self, global_displacements, uniform_local_load=None):
        return (
            self.local_stiffness() @ self.local_displacements(global_displacements)
            - self.equivalent_nodal_load_local(uniform_local_load)
        )

    def axial_tension_force(self, global_displacements, uniform_local_load=None):
        f = self.local_end_forces(global_displacements, uniform_local_load)
        return (f[3] - f[0]) / 2.0

    def section_forces_at(self, global_displacements, t, uniform_local_load=None, end_forces=None):
        """Return local axial, shear and bending moment at an element station.

        ``end_forces`` may pass this element's ``local_end_forces`` for the
        same displacements and load, already computed, to avoid recomputing."""
        t = min(max(float(t), 0.0), 1.0)
        L = self.length()
        x = t * L
        qx1, qy1, qx2, qy2 = self.load_components(uniform_local_load)
        f = end_forces if end_forces is not None else self.local_end_forces(global_displacements, uniform_local_load)
        return (
            -f[0] - qx1 * x - (qx2 - qx1) * x**2 / (2.0 * L),
            f[1] + qy1 * x + (qy2 - qy1) * x**2 / (2.0 * L),
            -f[2] + f[1] * x + 0.5 * qy1 * x**2 + (qy2 - qy1) * x**3 / (6.0 * L),
        )

    def local_displacement_at(self, global_displacements, t, uniform_local_load=None):
        """Interpolate displacement, including uniform-load curvature between nodes."""
        t = min(max(float(t), 0.0), 1.0)
        local = self.local_displacements(global_displacements)
        L = self.length()
        x = t * L
        u = (1.0 - t) * local[0] + t * local[3]
        n1 = 1.0 - 3.0 * t**2 + 2.0 * t**3
        n2 = L * (t - 2.0 * t**2 + t**3)
        n3 = 3.0 * t**2 - 2.0 * t**3
        n4 = L * (-t**2 + t**3)
        v = n1 * local[1] + n2 * local[2] + n3 * local[4] + n4 * local[5]
        qx1, qy1, qx2, qy2 = self.load_components(uniform_local_load)
        # Particular solution is zero at both ends (and has zero end slopes
        # for bending). Retain the curvature between the FE nodes.
        u += (qx1 * x * (L - x) / 2.0 + (qx2 - qx1) * x * (L**2 - x**2) / (6.0 * L)) / (self.E * self.A)
        v += x**2 * (L - x)**2 * (5.0 * qy1 + (qy2 - qy1) * (2.0 + x / L)) / (120.0 * self.E * self.I)
        return u, v

    def global_displacement_at(self, global_displacements, t, uniform_local_load=None):
        u, v = self.local_displacement_at(global_displacements, t, uniform_local_load)
        theta = self.angle()
        c = math.cos(theta)
        s = math.sin(theta)
        return c * u - s * v, s * u + c * v

    def force_summary(self, global_displacements, uniform_local_load=None):
        f = self.local_end_forces(global_displacements, uniform_local_load)

        # Positive internal axial force is tension, negative is compression.
        qx1, qy1, qx2, qy2 = self.load_components(uniform_local_load)
        station_candidates = [0.0, 1.0]
        L = self.length()
        # M extrema are roots of V(x), quadratic for a linear load.
        gradient = (qy2 - qy1) / L
        if abs(gradient) > 1e-12:
            disc = qy1**2 - 2.0 * gradient * f[1]
            roots = [] if disc < 0.0 else [(-qy1 + sign * math.sqrt(disc)) / gradient for sign in (-1, 1)]
        else:
            roots = [-f[1] / qy1] if abs(qy1) > 1e-12 else []
        for x in roots:
            if 0.0 < x < L:
                station_candidates.append(x / L)
        # V and N can also have interior extrema when a load changes sign.
        for q1, q2 in [(qx1, qx2), (qy1, qy2)]:
            if abs(q2 - q1) > 1e-12:
                t = -q1 / (q2 - q1)
                if 0.0 < t < 1.0:
                    station_candidates.append(t)
        station_forces = [
            self.section_forces_at(global_displacements, station, uniform_local_load, end_forces=f)
            for station in station_candidates
        ]
        axial_values = [item[0] for item in station_forces]
        shear_values = [item[1] for item in station_forces]
        moment_values = [item[2] for item in station_forces]
        compression_kn = max([max(-value, 0.0) for value in axial_values] + [0.0]) / 1000.0
        tension_kn = max([max(value, 0.0) for value in axial_values] + [0.0]) / 1000.0
        shear_kn = max([abs(value) for value in shear_values] + [0.0]) / 1000.0
        moment_knm = max([abs(value) for value in moment_values] + [0.0]) / 1_000_000.0

        return {
            "compression_kn": compression_kn,
            "tension_kn": tension_kn,
            "shear_kn": shear_kn,
            "moment_knm": moment_knm,
            "end_forces": f
    }


# ==========================================================
# STRUCTURE
# ==========================================================

def _element_end_global_load(element, xy, loads):
    """Global (fx, fy) line load, N/mm, at the end of ``element`` at point ``xy``."""
    stored = loads.get(element.id)
    if not stored:
        return 0.0, 0.0
    qx1, qy1, qx2, qy2 = element.load_components(stored)
    at_start = abs(element.start.x - xy[0]) < 1e-6 and abs(element.start.y - xy[1]) < 1e-6
    qx, qy = (qx1, qy1) if at_start else (qx2, qy2)
    theta = element.angle()
    c, s = math.cos(theta), math.sin(theta)
    return c * qx - s * qy, s * qx + c * qy


def _element_at(structure, groups, xy):
    """(element, end node) of the first element in ``groups`` with an end at ``xy``."""
    for element in structure.elements:
        if element.group not in groups:
            continue
        for node in (element.start, element.end):
            if abs(node.x - xy[0]) < 1e-6 and abs(node.y - xy[1]) < 1e-6:
                return element, node
    return None, None


def apply_eave_strip_loads(structure, strips, weighted_loads):
    """Point loads at the eave nodes for the envelope outside the frame lines.

    The eave height is the cladding pitching point, above the top chord node,
    and the roof runs past the column centreline to the wall cladding line.
    Each strip carries the wall above the node (``wall_mm`` of the column's
    top line load) and the roof out to the cladding (``roof_mm`` along the
    slope of the top chord's eave-end line load) to the eave node. A side
    whose canopy/lean-to roof starts at the eave node has no roof strip: the
    attachment's members already carry that roof.

    ``weighted_loads`` is [(element load dict, weight)]: the caller excludes
    loads that do not extend above the node (storage) by snapshot difference.
    """
    if not strips or not hasattr(structure, "element_uniform_loads"):
        return []
    arrows = []
    for strip in strips:
        xy = (strip["x"], strip["y"])
        parts = []
        column, node = _element_at(structure, {f"{strip['side']}_COLUMN"}, xy)
        if column is not None and strip["wall_mm"] > 1e-9:
            parts.append((column, strip["wall_mm"]))
        roof, roof_node = _element_at(structure, {"TOP"}, xy)
        canopy, _ = _element_at(structure, {f"{strip['side']}_CANOPY_TOP"}, xy)
        if roof is not None and canopy is None and strip["roof_mm"] > 1e-9:
            parts.append((roof, strip["roof_mm"]))
            node = node or roof_node
        fx = fy = 0.0
        for element, length in parts:
            for loads, weight in weighted_loads:
                ex, ey = _element_end_global_load(element, xy, loads)
                fx += weight * ex * length
                fy += weight * ey * length
        if node is None or (abs(fx) < 1e-9 and abs(fy) < 1e-9):
            continue
        structure.add_load(node.ux, fx)
        structure.add_load(node.uy, fy)
        arrows.append((node.x, node.y, fx, fy))
    return arrows


class Structure2D:

    def __init__(self):
        self.nodes = []
        self.elements = []
        self.loads = {}
        self.element_uniform_loads = {}
        self.springs = {}
        # (dofs, stiffness) springs on the mean movement of several DOFs.
        self.sway_springs = []
        self.restrained_dofs = []

    def add_node(self, node):
        self.nodes.append(node)
        for dof in node.unused_native_dofs:
            self.add_support(dof)

    def add_element(self, element):
        self.elements.append(element)

    def add_support(self, dof):
        if dof not in self.restrained_dofs:
            self.restrained_dofs.append(dof)

    def add_load(self, dof, value):
        self.loads[dof] = self.loads.get(dof, 0.0) + value

    def clear_loads(self):
        self.loads = {}
        self.element_uniform_loads = {}

    def uniform_load_for_element(self, element):
        return self.element_uniform_loads.get(element.id, (0.0, 0.0))

    def element_load_snapshot(self):
        return {
            int(element_id): tuple(float(value) for value in load)
            for element_id, load in self.element_uniform_loads.items()
        }

    def restore_element_load_snapshot(self, snapshot):
        self.element_uniform_loads = {
            int(element_id): tuple(float(value) for value in load)
            for element_id, load in (snapshot or {}).items()
        }

    def add_uniform_global_line_load(self, element, fx_n_per_mm=0.0, fy_n_per_mm=0.0):
        """Apply a uniform global line load to one frame element."""
        theta = element.angle()
        c = math.cos(theta)
        s = math.sin(theta)
        incremental_local = (
            c * fx_n_per_mm + s * fy_n_per_mm,
            -s * fx_n_per_mm + c * fy_n_per_mm,
        )
        existing = self.uniform_load_for_element(element)
        self.element_uniform_loads[element.id] = tuple(
            value + incremental_local[index % 2] for index, value in enumerate(existing)
        )
        local_vector = element.equivalent_nodal_load_local(incremental_local)
        global_vector = element.transformation_matrix().T @ local_vector
        for dof, value in zip(element.dof_indices(), global_vector):
            self.add_load(dof, value)

    def add_linear_global_line_load(self, element, fx_start=0.0, fy_start=0.0, fx_end=0.0, fy_end=0.0):
        """Apply linear global N/mm loading, retaining member load recovery."""
        theta = element.angle()
        c, s = math.cos(theta), math.sin(theta)
        incremental = (c*fx_start+s*fy_start, -s*fx_start+c*fy_start,
                       c*fx_end+s*fy_end, -s*fx_end+c*fy_end)
        existing = element.load_components(self.uniform_load_for_element(element))
        self.element_uniform_loads[element.id] = tuple(a+b for a,b in zip(existing, incremental))
        vector = element.transformation_matrix().T @ element.equivalent_nodal_load_local(incremental)
        for dof, value in zip(element.dof_indices(), vector):
            self.add_load(dof, value)

    def add_uniform_global_line_load_between(
        self,
        base_node,
        top_node,
        fx_n_per_mm=0.0,
        fy_n_per_mm=0.0,
        element_groups=None,
    ):
        """Load every collinear member segment between two column nodes."""
        groups = None if element_groups is None else {str(group) for group in element_groups}
        x1, y1 = float(base_node.x), float(base_node.y)
        x2, y2 = float(top_node.x), float(top_node.y)
        dx = x2 - x1
        dy = y2 - y1
        length = math.hypot(dx, dy)
        if length <= 1e-9:
            return []

        def on_target_line(node):
            cross = (node.x - x1) * dy - (node.y - y1) * dx
            station = ((node.x - x1) * dx + (node.y - y1) * dy) / (length**2)
            return abs(cross) <= max(length, 1.0) * 1e-6 and -1e-6 <= station <= 1.0 + 1e-6

        matched = []
        for element in self.elements:
            if groups is not None and element.group not in groups:
                continue
            if not (on_target_line(element.start) and on_target_line(element.end)):
                continue
            self.add_uniform_global_line_load(element, fx_n_per_mm, fy_n_per_mm)
            matched.append(element)
        return matched

    def element_force_summary(self, element, displacements):
        return element.force_summary(displacements, self.uniform_load_for_element(element))

    def element_global_displacement_at(self, element, displacements, t):
        return element.global_displacement_at(
            displacements,
            t,
            self.uniform_load_for_element(element),
        )

    def add_spring(self, dof, stiffness_n_per_mm):
        if stiffness_n_per_mm > 0.0:
            self.springs[dof] = self.springs.get(dof, 0.0) + stiffness_n_per_mm

    def add_sway_spring(self, dofs, stiffness_n_per_mm):
        """Spring on the mean movement of several DOFs, e.g. both eave UX.

        The force k * mean(u) is shared equally, so frame sway is resisted but
        equal and opposite movements (eave spread under gravity) are not.
        """
        if stiffness_n_per_mm > 0.0 and dofs:
            self.sway_springs.append((tuple(dofs), float(stiffness_n_per_mm)))

    def spring_reaction_vector(self, displacements):
        R = np.zeros(len(displacements))
        for dof, stiffness in self.springs.items():
            R[dof] += stiffness * displacements[dof]
        for dofs, stiffness in getattr(self, "sway_springs", ()):
            share = 1.0 / len(dofs)
            force = stiffness * share * sum(displacements[dof] for dof in dofs)
            for dof in dofs:
                R[dof] += force * share
        return R

    def elastic_stiffness_matrix(self):
        total_dofs = len(self.nodes) * 3
        K = np.zeros((total_dofs, total_dofs))
        for element in self.elements:
            k = element.global_stiffness()
            dofs = element.dof_indices()
            for i in range(6):
                for j in range(6):
                    K[dofs[i], dofs[j]] += k[i, j]
        for dof, stiffness in self.springs.items():
            K[dof, dof] += stiffness
        for dofs, stiffness in getattr(self, "sway_springs", ()):
            share = 1.0 / len(dofs)
            for a in dofs:
                for b in dofs:
                    K[a, b] += stiffness * share * share
        return K

    def load_vector(self):
        total_dofs = len(self.nodes) * 3
        F = np.zeros(total_dofs)
        for dof, value in self.loads.items():
            F[dof] += value
        return F

    def solve_with_stiffness(self, K, F):
        total_dofs = len(self.nodes) * 3
        free_dofs = [i for i in range(total_dofs) if i not in self.restrained_dofs]
        Kff = K[np.ix_(free_dofs, free_dofs)]
        Ff = F[free_dofs]

        df = np.linalg.solve(Kff, Ff)
        d = np.zeros(total_dofs)
        d[free_dofs] = df
        return d

    def solve_load_matrix_with_stiffness(self, K, load_matrix):
        total_dofs = len(self.nodes) * 3
        free_dofs = [i for i in range(total_dofs) if i not in self.restrained_dofs]
        Kff = K[np.ix_(free_dofs, free_dofs)]
        Ff = load_matrix[free_dofs, :]

        df = np.linalg.solve(Kff, Ff)
        displacements = np.zeros((total_dofs, load_matrix.shape[1]))
        displacements[free_dofs, :] = df
        return displacements

    def solve(self):
        K = self.elastic_stiffness_matrix()
        F = self.load_vector()
        d = self.solve_with_stiffness(K, F)
        R = K @ d - F + self.spring_reaction_vector(d)
        return d, R, F

    def geometric_stiffness_matrix(self, displacements):
        total_dofs = len(self.nodes) * 3
        K = np.zeros((total_dofs, total_dofs))
        for element in self.elements:
            axial_tension_n = element.axial_tension_force(
                displacements,
                self.uniform_load_for_element(element),
            )
            kg = element.global_geometric_stiffness(axial_tension_n)
            dofs = element.dof_indices()
            for i in range(6):
                for j in range(6):
                    K[dofs[i], dofs[j]] += kg[i, j]
        return K

    def solve_second_order(self, max_iterations=20, tolerance=1e-4):
        elastic_k = self.elastic_stiffness_matrix()
        F = self.load_vector()
        first_order_d = self.solve_with_stiffness(elastic_k, F)
        d = first_order_d.copy()
        converged = False
        iterations = 0

        for iteration in range(1, max_iterations + 1):
            tangent_k = elastic_k + self.geometric_stiffness_matrix(d)
            next_d = self.solve_with_stiffness(tangent_k, F)
            change = np.max(np.abs(next_d - d))
            scale = max(np.max(np.abs(next_d)), 1.0)
            iterations = iteration
            d = next_d
            if change / scale <= tolerance:
                converged = True
                break

        R = elastic_k @ d - F + self.spring_reaction_vector(d)
        first_max = max(float(np.max(np.abs(first_order_d))), 0.0)
        second_max = max(float(np.max(np.abs(d))), 0.0)
        amplification = second_max / first_max if first_max > 1e-12 else 1.0
        info = {
            "type": "second_order",
            "converged": converged,
            "iterations": iterations,
            "max_iterations": max_iterations,
            "tolerance": tolerance,
            "first_order_max_abs_mm": first_max,
            "second_order_max_abs_mm": second_max,
            "displacement_amplification": amplification,
        }
        return d, R, F, info
