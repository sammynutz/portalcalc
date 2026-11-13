"""Small-displacement 3D axial network with elastic portal interface blocks.

Units: N, mm, MPa. Rods use a unilateral elastic law, N = EA/L max(extension, 0).
No penalty supports, compression stiffness in slack rods, sizing or code checks.
"""
from dataclasses import dataclass, replace
import copy
import math

import numpy as np

from bracing_layout import brace_group


class AnalysisError(ValueError):
    pass


def positive(value, name):
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise AnalysisError(f"{name} must be finite and positive") from None
    if isinstance(value, bool) or not math.isfinite(result) or result <= 0:
        raise AnalysisError(f"{name} must be finite and positive")
    return result


def elastic_solve(matrix, force):
    """Equilibrated positive-definite solve; reject mechanisms, never pin them."""
    return _solve_factor(_elastic_factor(matrix), force)


def _elastic_factor(matrix):
    diagonal = np.diag(matrix)
    if not len(diagonal) or np.any(diagonal <= 0) or not np.all(np.isfinite(matrix)):
        raise AnalysisError("Unstable model: a free movement has no stiffness")
    scale = np.sqrt(diagonal)
    scaled = matrix / scale[:, None] / scale[None, :]
    try:
        factor = np.linalg.cholesky((scaled + scaled.T) * .5)
        if np.min(np.diag(factor)) < 1e-6:
            raise np.linalg.LinAlgError("near mechanism")
    except np.linalg.LinAlgError:
        raise AnalysisError("Unstable or ill-conditioned model: check brace continuity and supports") from None
    return scale, factor


def _solve_factor(factored, force):
    scale, factor = factored
    rhs = np.asarray(force) / (scale if np.ndim(force) == 1 else scale[:, None])
    answer = np.linalg.solve(factor.T, np.linalg.solve(factor, rhs))
    return answer / (scale if answer.ndim == 1 else scale[:, None])


@dataclass(frozen=True)
class AxialMember:
    id: str
    start: int
    end: int
    area_mm2: float
    modulus_mpa: float = 200000.
    tension_only: bool = False
    kind: str = "strut"
    panel_id: str = ""


class AxialModel3D:
    def __init__(self):
        self.nodes = []
        self.node_labels = []
        self.fixed = set()
        self.members = []
        self.blocks = []

    def add_node(self, point, label="", fixed=False):
        vector = np.asarray(point, dtype=float)
        if vector.shape != (3,) or not np.all(np.isfinite(vector)):
            raise AnalysisError("A node needs three finite coordinates")
        index = len(self.nodes)
        self.nodes.append(tuple(float(x) for x in vector))
        self.node_labels.append(label or f"Node {index + 1}")
        if fixed:
            self.fixed.update(range(3 * index, 3 * index + 3))
        return index

    def add_member(self, member):
        if member.id in {m.id for m in self.members}:
            raise AnalysisError(f"Duplicate member ID: {member.id}")
        if any(isinstance(i, bool) or not isinstance(i, int) or not 0 <= i < len(self.nodes)
               for i in (member.start, member.end)):
            raise AnalysisError("Unknown axial-member endpoint")
        positive(member.area_mm2, "Member area")
        positive(member.modulus_mpa, "Elastic modulus")
        if np.linalg.norm(np.subtract(self.nodes[member.end], self.nodes[member.start])) < 1e-6:
            raise AnalysisError("Zero-length axial member")
        self.members.append(replace(member, area_mm2=float(member.area_mm2), modulus_mpa=float(member.modulus_mpa)))

    def add_block(self, dofs, stiffness):
        indices = np.asarray(dofs, dtype=int)
        k = np.asarray(stiffness, dtype=float)
        if (len(set(indices)) != len(indices) or np.any(indices < 0)
                or np.any(indices >= 3 * len(self.nodes)) or k.shape != (len(indices), len(indices))
                or not np.all(np.isfinite(k)) or not np.allclose(k, k.T, rtol=1e-9, atol=1e-6)):
            raise AnalysisError("Invalid portal interface stiffness block")
        self.blocks.append((indices, (k + k.T) * .5))

    def _prepare(self):
        size = 3 * len(self.nodes)
        # Load-independent geometry/assembly and active-set factors are shared
        # by the simultaneous cases of one selected arrangement. Include fixed
        # DOFs because diaphragm alignment also changes them directly.
        key = (tuple(self.nodes),tuple(self.members),tuple(sorted(self.fixed)),
               tuple((tuple(dofs),k.tobytes()) for dofs,k in self.blocks))
        if getattr(self,'_prepared_key',None)==key:
            return self._prepared
        free = np.array([i for i in range(size) if i not in self.fixed], dtype=int)
        if len(free) > 600:
            raise AnalysisError("This reduced solver supports up to 600 free DOFs; use fewer bays/panels")
        linear = np.zeros((size, size))
        for dofs, k in self.blocks:
            linear[np.ix_(dofs, dofs)] += k
        rows, stiffnesses, rods = [], [], []
        lengths = []
        for member in self.members:
            delta = np.subtract(self.nodes[member.end], self.nodes[member.start])
            length = float(np.linalg.norm(delta))
            row = np.zeros(size)
            row[3 * member.start:3 * member.start + 3] = -delta / length
            row[3 * member.end:3 * member.end + 3] = delta / length
            k = member.modulus_mpa * member.area_mm2 / length
            rows.append(row)
            stiffnesses.append(k)
            lengths.append(length)
            if member.tension_only:
                rods.append(len(rows) - 1)
            else:
                linear += k * np.outer(row, row)
        rows = np.asarray(rows).reshape((-1, size))
        ks = np.asarray(stiffnesses)
        rod_rows = rows[rods][:, free]
        rod_ks = ks[rods]
        base = linear[np.ix_(free, free)]
        full = base + rod_rows.T @ (rod_ks[:, None] * rod_rows)
        prepared = (free,linear,rows,ks,rods,lengths,rod_rows,rod_ks,base,full,
                    {None:_elastic_factor(full)})
        self._prepared_key,self._prepared=key,prepared
        return prepared

    def solve(self, loads, max_iterations=160, allow_neutral_equilibrium=False):
        size = 3 * len(self.nodes)
        force = np.asarray(loads, dtype=float)
        if force.shape != (size,) or not np.all(np.isfinite(force)):
            raise AnalysisError("A load case needs one finite load per translational DOF")
        free,linear,rows,ks,rods,lengths,rod_rows,rod_ks,base,full,factors=self._prepare()

        def active_solve(active, rhs):
            key = None if active is None else active.tobytes()
            if key not in factors:
                matrix=base+rod_rows[active].T@(rod_ks[active,None]*rod_rows[active])
                # Bound memory for large layouts with many different rod
                # active sets; retain the all-rods factor used by line search.
                if len(factors)>=32:
                    del factors[next(k for k in factors if k is not None)]
                try:
                    factors[key]=_elastic_factor(matrix)
                except AnalysisError:
                    factors[key]=None
            if factors[key] is None:
                raise AnalysisError('Unstable or ill-conditioned model: check brace continuity and supports')
            return _solve_factor(factors[key],rhs)

        f = force[free]
        # The all-rods system is an upper stiffness bound. If it is unstable,
        # removing compressive rods cannot produce a stable network.
        initial = active_solve(None, f)
        u = np.zeros(len(free))
        force_tolerance = max(1e-6, np.max(np.abs(f), initial=0) * 1e-8)

        def state(value):
            extensions = rod_rows @ value
            tensions = rod_ks * np.maximum(extensions, 0.)
            gradient = base @ value + rod_rows.T @ tensions - f
            energy = .5 * value @ base @ value + .5 * np.dot(tensions, np.maximum(extensions, 0.)) - f @ value
            return extensions, tensions, gradient, energy

        converged = False
        for iteration in range(1, max_iterations + 1):
            extension, tension, gradient, energy = state(u)
            if np.max(np.abs(gradient), initial=0) <= force_tolerance:
                converged = True
                break
            active = extension >= -1e-10
            try:
                direction = initial if iteration == 1 else active_solve(active, -gradient)
            except AnalysisError:
                # A search direction only: the physical force law and final
                # equilibrium always use zero compression force in slack rods.
                if allow_neutral_equilibrium:
                    # Find a gravity equilibrium step in the supported
                    # subspace without adding stiffness. Neutral cases retain
                    # an unverified tangent and cannot pass design checks.
                    tangent = base + rod_rows[active].T @ (rod_ks[active, None] * rod_rows[active])
                    diagonal=np.sqrt(np.maximum(np.diag(tangent),1e-30))
                    direction=np.linalg.lstsq(tangent/diagonal[:,None]/diagonal[None,:],
                                               -gradient/diagonal,rcond=1e-12)[0]/diagonal
                    if np.max(np.abs(tangent@direction+gradient),initial=0)>force_tolerance:
                        direction=active_solve(None,-gradient)
                else:
                    direction = active_solve(None, -gradient)
            slope = float(gradient @ direction)
            if not math.isfinite(slope) or slope >= 0:
                raise AnalysisError("Tension-only analysis could not find an equilibrium direction")
            step = 1.
            energy_roundoff = 32*np.finfo(float).eps*(abs(energy)+abs(f @ u)+1.)
            for _ in range(45):
                candidate = u + step * direction
                if state(candidate)[3] <= energy + 1e-4 * step * slope + energy_roundoff:
                    break
                step *= .5
            else:
                raise AnalysisError("Tension-only analysis did not converge")
            u = candidate
        if not converged:
            raise AnalysisError(f"Tension-only analysis did not converge; residual {np.max(np.abs(state(u)[2]),initial=0):.6g} N, tolerance {force_tolerance:.6g} N; layout may have a mechanism")
        extension, tension, gradient, _ = state(u)
        active = extension >= -1e-10
        # Zero-force and slack rods have only one-sided stiffness, so stability
        # is judged on the taut rods. A zero-stiffness mode left by them is not
        # a mechanism if every movement along it extends some other rod (at
        # once for zero-force rods, after the gap for slack ones); report the
        # play instead. Acceptance is decided in the design checks.
        taut = extension > 1e-10
        slack_play = None
        try:
            active_solve(taut, np.zeros(len(free)))
            tangent_status = "positive_with_taut_rods"
        except AnalysisError:
            taut_tangent = base + rod_rows[taut].T @ (rod_ks[taut, None] * rod_rows[taut])
            slack_play = bounded_slack_play(taut_tangent, rod_rows, extension, taut)
            try:
                active_solve(active, np.zeros(len(free)))
                slack_rods_only = False
            except AnalysisError:
                # Even the zero-force rods do not stiffen it: rods are slack.
                slack_rods_only = True
                if slack_play is None and not allow_neutral_equilibrium:
                    raise AnalysisError("Unstable model: a mechanism remains with every slack rod considered; "
                                        "check brace continuity and supports") from None
            tangent_status = ("slack_play_stability_unverified" if slack_rods_only and not allow_neutral_equilibrium
                              else "neutral_rods_required_stability_unverified")
        displacement = np.zeros(size)
        displacement[free] = u
        extensions = rows @ displacement
        forces = ks * extensions
        forces[rods] = ks[rods] * np.maximum(extensions[rods], 0.)
        internal = linear @ displacement
        if rods:
            internal += rows[rods].T @ forces[rods]
        reaction = internal - force
        residual = float(np.max(np.abs(reaction[free]), initial=0))
        if residual > force_tolerance * 2:
            raise AnalysisError("3D free-DOF equilibrium check failed")
        return {
            "displacements_mm": displacement.reshape((-1, 3)).tolist(),
            "support_reactions_n": [{"node": i, "label": self.node_labels[i],
                                     "point_mm": self.nodes[i], "force_n": reaction[3*i:3*i+3].tolist()}
                                    for i in range(len(self.nodes)) if 3*i in self.fixed],
            "members": [{"id": m.id, "kind": m.kind, "panel_id": m.panel_id,
                         "start": m.start, "end": m.end, "length_mm": lengths[i],
                         "area_mm2": m.area_mm2, "modulus_mpa": m.modulus_mpa,
                         "axial_n": float(forces[i]), "extension_mm": float(extensions[i]),
                         "tension_only": m.tension_only,
                         "state": "slack" if m.tension_only and forces[i] <= force_tolerance else "tension" if forces[i] >= 0 else "compression"}
                        for i, m in enumerate(self.members)],
            "iterations": iteration, "free_dofs": len(free), "residual_n": residual,
            "tangent_status": tangent_status,
            **({"slack_play": {**slack_play, "rods": [self.members[rods[i]].id for i in slack_play["rods"]]}}
               if slack_play is not None else {}),
        }


def bounded_slack_play(tangent, rod_rows, extension, taut, tolerance=1e-8):
    """Play before zero-force or slack rods engage along zero-stiffness modes, or None.

    The modes are the null space of ``tangent`` (the taut rods). The other
    rods can take up a movement that extends them: zero-force rods at once,
    slack rods after their gap. Each mode is checked both ways; a direction
    that extends no rod is a mechanism (None). With several modes, a
    combination could still escape every rod, so ``directions_verified``
    records an exact test of all combinations (Stiemke's lemma). The play is
    the movement before the first rod engages, with each mode scaled to 1 mm
    largest component; with several modes it is taken along the modes.
    """
    scale = np.sqrt(np.maximum(np.diag(tangent), 1e-30))
    values, vectors = np.linalg.eigh(tangent / scale[:, None] / scale[None, :])
    modes = vectors[:, values < tolerance] / scale[:, None]
    modes = modes / np.max(np.abs(modes), axis=0, initial=1e-30)
    others = np.where(~taut)[0]
    gaps_all = np.maximum(-extension[others], 0.)
    rates = rod_rows[others] @ modes
    plays, engaged = [], set()
    for j in range(modes.shape[1]):
        for sign in (1., -1.):
            rate = sign * rates[:, j]
            engaging = rate > 1e-9
            if not engaging.any():
                return None
            gaps = gaps_all[engaging] / rate[engaging]
            plays.append(float(np.min(gaps)))
            engaged.update(int(i) for i in others[engaging][gaps <= np.min(gaps)*(1+1e-6)+1e-12])
    verified = modes.shape[1] <= 1 or no_escaping_direction(rates)
    neutral = int(np.sum(np.abs(extension[others]) <= 1e-10))
    return {"modes": int(modes.shape[1]), "play_mm": max(plays, default=0.), "rods": sorted(engaged),
            "directions_verified": bool(verified), "zero_force_rods": neutral,
            "basis": "Zero-stiffness modes of the taut rods; every movement along them extends a zero-force or slack rod "
                     "after the stated play (mode scaled to 1 mm largest movement)"
                     + ("" if verified else "; a combination of modes may escape every rod") + ". Stability unverified."}


def no_escaping_direction(rates, tolerance=1e-9):
    """True if every non-zero combination ``a`` of modes extends some rod.

    ``rates`` holds each rod's extension rate per unit mode (rods x modes). By
    Stiemke's lemma no ``a`` has ``rates @ a <= 0`` (other than zero) exactly
    when the rates have full column rank and a strictly positive weighting of
    the rods balances them, ``rates.T @ y = 0`` with ``y > 0``. That is found as
    ``y = 1 + z``, ``z >= 0``, by non-negative least squares.
    """
    rows = rates[np.any(np.abs(rates) > 1e-12, axis=1)]
    if not len(rows) or np.linalg.matrix_rank(rows, tol=1e-9*np.abs(rows).max()) < rates.shape[1]:
        return False
    unit = rows / np.linalg.norm(rows, axis=1)[:, None]
    ones = np.ones(len(unit))
    z = _nnls(unit.T, -unit.T @ ones)
    return float(np.linalg.norm(unit.T @ (ones + z))) <= tolerance * np.sqrt(len(unit))


def _nnls(a, b, max_iterations=None):
    """Lawson-Hanson non-negative least squares: min ||a x - b||, x >= 0."""
    n = a.shape[1]
    x = np.zeros(n)
    passive = np.zeros(n, dtype=bool)
    small = 10 * np.finfo(float).eps * max(a.shape) * max(np.abs(a).sum(axis=0).max(initial=0.), 1.)
    for _ in range(max_iterations or 3*n+10):
        gradient = a.T @ (b - a @ x)
        if passive.all() or np.max(np.where(passive, -np.inf, gradient)) <= small:
            break
        passive[np.argmax(np.where(passive, -np.inf, gradient))] = True
        for _ in range(n+1):
            trial = np.zeros(n)
            trial[passive] = np.linalg.lstsq(a[:, passive], b, rcond=None)[0]
            if np.all(trial[passive] > small):
                break
            blocking = passive & (trial <= small)
            step = np.min(x[blocking] / np.maximum(x[blocking] - trial[blocking], 1e-300))
            x = x + step * (trial - x)
            passive &= x > small
            x[~passive] = 0.
        x = trial
    return x


class PortalReduction:
    """Static condensation onto work-conjugate point translations.

    H maps portal DOFs onto X/Z point translations. A QR basis separates
    prescribed interface movement from internal coordinates in null(H).
    Condensing only the internal coordinates permits an unbraced sway mechanism
    in the portal: the assembled bracing network must stabilise it. Cubic beam
    interpolation retains rotation contributions at non-nodal attachment points.
    """
    def __init__(self, structure, attachment_points, loads=None):
        self.structure = structure
        self.points = tuple(attachment_points)
        self.k = structure.elastic_stiffness_matrix()
        size = len(self.k)
        self.free = np.array([i for i in range(size) if i not in structure.restrained_dofs], dtype=int)
        self.loads = np.zeros(size) if loads is None else np.asarray(loads, dtype=float)
        if self.loads.shape != (size,) or not np.all(np.isfinite(self.loads)):
            raise AnalysisError("Invalid portal load vector")
        h = np.zeros((2 * len(self.points), size))
        self.attachment_hosts = []
        # Main roof members first, so a point at the eave keeps its main-roof
        # host; open canopy roof members host the canopy edge points.
        top = ([element for element in structure.elements if element.group == "TOP"]
               + [element for element in structure.elements if element.group in ("LEFT_CANOPY_TOP", "RIGHT_CANOPY_TOP")])
        for index, point in enumerate(self.points):
            found = False
            p = np.asarray(point, dtype=float)
            for element in top:
                a = np.array([element.start.x, element.start.y])
                delta = np.array([element.end.x, element.end.y]) - a
                length = float(np.linalg.norm(delta))
                t = float(np.dot(p-a, delta) / length**2)
                if not -1e-8 <= t <= 1+1e-8 or np.linalg.norm(p-a-t*delta) > 1e-3:
                    continue
                t = min(1., max(0., t))
                local = np.array([[1-t, 0, 0, t, 0, 0],
                                  [0, 1-3*t*t+2*t**3, length*(t-2*t*t+t**3),
                                   0, 3*t*t-2*t**3, length*(-t*t+t**3)]])
                c, s = delta / length
                rotate = np.array([[c, -s], [s, c]])
                mapping = rotate @ local @ element.transformation_matrix()
                for j, dof in enumerate(element.dof_indices()):
                    h[2*index:2*index+2, dof] += mapping[:, j]
                self.attachment_hosts.append((element, t, mapping))
                found = True
                break
            if not found:
                raise AnalysisError(f"Portal roof attachment {point} does not lie on a TOP or canopy roof element")
        self.h = h
        hf = h[:, self.free]
        kff = self.k[np.ix_(self.free, self.free)]
        # Normalise rotations by a representative length for a well-scaled QR.
        reference = max(1., max(e.length() for e in structure.elements))
        coordinate_scale = np.where(self.free % 3 == 2, 1/reference, 1.)
        count = len(h)
        q, r = np.linalg.qr((hf * coordinate_scale).T, mode="complete")
        leading = r[:count]
        if (len(self.free) < count or leading.shape != (count, count)
                or np.min(np.abs(np.diag(leading)), initial=1.) < 1e-10):
            raise AnalysisError("The roof attachment grid cannot be resolved by this portal mesh; use fewer end-wall panels or a finer 2D roof mesh") from None
        prescribed = coordinate_scale[:, None] * (q[:, :count] @ np.linalg.solve(leading.T, np.eye(count)))
        internal = coordinate_scale[:, None] * q[:, count:]
        kii = internal.T @ kff @ internal
        kiu = internal.T @ kff @ prescribed
        if internal.shape[1]:
            # Retain the load response as well as the stiffness reduction. A
            # loaded portal must recover its internal deformation, not merely
            # receive an approximate force at the roof attachment points.
            solved = elastic_solve(kii, np.column_stack((kiu, internal.T)))
            self.recovery = prescribed - internal @ solved[:, :count]
            self.load_flexibility = internal @ solved[:, count:]
            self.stiffness = prescribed.T @ kff @ prescribed - kiu.T @ solved[:, :count]
        else:
            self.recovery = prescribed
            self.load_flexibility = np.zeros((len(self.free), len(self.free)))
            self.stiffness = prescribed.T @ kff @ prescribed
        self.particular = self.load_flexibility @ self.loads[self.free]
        self.equivalent_load = self.recovery.T @ self.loads[self.free]
        self.member_equivalent_loads = {}
        self.stiffness = (self.stiffness + self.stiffness.T) * .5

    def with_loads(self, loads, member_equivalent_loads=None):
        """Reuse one stiffness reduction for a case-specific full portal load."""
        loaded = copy.copy(self)
        loaded.loads = np.asarray(loads, dtype=float)
        if loaded.loads.shape != self.loads.shape or not np.all(np.isfinite(loaded.loads)):
            raise AnalysisError("Invalid portal load vector")
        loaded.particular = self.load_flexibility @ loaded.loads[self.free]
        loaded.equivalent_load = self.recovery.T @ loaded.loads[self.free]
        loaded.member_equivalent_loads = member_equivalent_loads or {}
        return loaded

    def recover(self, translations, member_forces=True):
        """Recovered portal state. ``member_forces=False`` omits the per-member
        end-force evidence (report/export only) for search checks."""
        movement = np.asarray(translations, dtype=float)
        interface_force = self.stiffness @ movement - self.equivalent_load
        d = np.zeros(len(self.k))
        d[self.free] = self.particular + self.recovery @ movement
        residual = self.k @ d - self.loads - self.h.T @ interface_force
        if np.max(np.abs(self.h @ d - movement), initial=0) > max(1e-6, np.max(np.abs(movement), initial=0)*1e-7):
            raise AnalysisError("Portal displacement recovery compatibility failed")
        member_point_loads = {}
        point_loads = []
        for index, (element, t, mapping) in enumerate(self.attachment_hosts):
            point_force = interface_force[2*index:2*index+2]
            if 1e-8 < t < 1-1e-8:
                # An interior point load has a consistent member load vector.
                # Subtract it from k*d to recover physical end forces. Exact
                # nodal attachments are joint loads, not member line loads.
                local_vector = element.transformation_matrix() @ mapping.T @ point_force
                member_point_loads[element.id] = member_point_loads.get(element.id, np.zeros(6)) + local_vector
                point_loads.append({"element": element.id, "fraction": t,
                                    "x_mm": self.points[index][0], "z_mm": self.points[index][1],
                                    "fx_n": float(point_force[0]), "fz_n": float(point_force[1]),
                                    "equivalent_local_n_n_nmm": local_vector.tolist()})
        supports = []
        fixed = set(self.structure.restrained_dofs)
        springs = self.structure.springs
        for node in self.structure.nodes:
            # Native duplicate translational DOFs at pinned joints are not
            # physical supports. Only report reactions which actually occur.
            values = []
            for dof in (node.native_ux, node.native_uy, node.native_rz):
                value = float(residual[dof]) if dof in fixed else 0.
                value -= springs.get(dof, 0.) * d[dof]
                values.append(value)
            if max(abs(x) for x in values) > 1e-7:
                supports.append({"node": node.id, "x_mm": node.x, "z_mm": node.y,
                                 "fx_n": values[0], "fz_n": values[1], "m_xz_nmm": values[2]})
        recovered = {
            "interface_forces_n": interface_force.reshape((-1, 2)).tolist(),
            "interior_attachment_point_loads": point_loads,
            "support_reactions": supports,
            "node_displacements": [{"node": n.id, "x_mm": n.x, "z_mm": n.y,
                                    "ux_mm": float(d[n.ux]), "uz_mm": float(d[n.uy]),
                                    "rotation_xz_rad": float(d[n.rz])} for n in self.structure.nodes],
        }
        if not member_forces:
            return recovered
        return {
            **recovered,
            "member_end_forces": [{"element": e.id, "group": e.group, "section": e.profile.name,
                                   "local_n_n_nmm": (e.local_end_forces(d)
                                       -member_point_loads.get(e.id, np.zeros(6))
                                       -np.asarray(self.member_equivalent_loads.get(str(e.id), self.member_equivalent_loads.get(e.id, np.zeros(6))))).tolist()}
                                  for e in self.structure.elements],
        }


CANOPY_EDGE = "canopy edge"   # node label suffix for open-canopy edge nodes


def building_sway_node(label, height_mm):
    """True for a node whose longitudinal movement is global building sway.

    Global sway is measured on the main building above ground (portal roof
    nodes over the columns and roof), not at open canopy edges: a canopy edge
    moves with its own canopy bracing beyond the column line.
    """
    return height_mm > 0 and not str(label).endswith(CANOPY_EDGE)


def build_braced_building(layout, portal, strut_area_mm2, roof_brace_area_mm2,
                         wall_brace_area_mm2, end_brace_area_mm2, end_post_area_mm2,
                         end_post_faces=("front_end", "back_end"), strut_overrides=None, brace_moduli=None,
                         canopy_brace_area_mm2=None, portal_brace_sections=None):
    """Assemble the braced building network.

    ``portal_brace_sections`` is ``{'column': profile, 'beam': profile}`` for the
    layout's portal frame braces; each is condensed onto the Y translations of
    its two eave nodes (``bracing_portal_brace``) and listed in
    ``model.portal_braces`` for recovery.
    """
    if not layout.selection:
        raise AnalysisError("Pin the brace panels to analyse in the 3D location editor first. Available panels are not installed braces.")
    if np.max(np.abs(portal.equivalent_load), initial=0) > 1e-8:
        raise AnalysisError("This building-analysis stage requires an unloaded portal macro")
    g = layout.geometry
    if len(portal.points) != len(g.roof_stations) + len(g.canopies):
        raise AnalysisError("The portal attachment points must be the roof stations followed by each canopy edge")
    if (len(g.roof_stations) + len(g.canopies)) * len(g.bay_stations_mm) * 3 > 600:
        raise AnalysisError("This reduced solver supports up to 600 roof DOFs; use fewer bays/panels")
    model = AxialModel3D()
    point_nodes = {}

    def node(point, label="", fixed=False):
        key = tuple(round(float(v), 6) for v in point)
        if key not in point_nodes:
            point_nodes[key] = model.add_node(point, label, fixed)
        index = point_nodes[key]
        if fixed:
            model.fixed.update(range(3*index, 3*index+3))
        return index

    roof_grid = []
    portal_dofs = []
    for i, y in enumerate(g.bay_stations_mm):
        roof = [node((x, y, g.roof_z(x)), f"Portal {i+1}, roof x={x/1000:g} m") for x in g.roof_stations]
        # Canopy edge nodes follow the roof stations, in the portal point order.
        for side, _, _ in g.canopies:
            tip_x, tip_z = g.canopy_tip(side)
            roof.append(node((tip_x, y, tip_z), f"Portal {i+1}, {side} {CANOPY_EDGE}"))
        dofs = [dof for index in roof for dof in (3*index, 3*index+2)]
        model.add_block(dofs, portal.stiffness)
        roof_grid.append(roof)
        portal_dofs.append(dofs)
    for i, (y0, y1) in enumerate(zip(g.bay_stations_mm, g.bay_stations_mm[1:])):
        for j, x in enumerate(g.roof_stations):
            identifier = f"strut/bay/{i}/line/{j}"
            properties = (strut_overrides or {}).get(identifier, {})
            model.add_member(AxialMember(identifier, roof_grid[i][j], roof_grid[i+1][j],
                                        positive(properties.get("area_mm2", strut_area_mm2), "Strut area"),
                                        modulus_mpa=positive(properties.get("modulus_mpa", 200000.), "Strut modulus"), kind="longitudinal_strut"))
        # The canopy edge purlin is the strut along each canopy's outer edge;
        # canopy edge lines are numbered after the roof stations.
        for k, (side, _, _) in enumerate(g.canopies):
            j = len(g.roof_stations) + k
            identifier = f"strut/bay/{i}/line/{j}"
            properties = (strut_overrides or {}).get(identifier, {})
            model.add_member(AxialMember(identifier, roof_grid[i][j], roof_grid[i+1][j],
                                        positive(properties.get("area_mm2", strut_area_mm2), "Canopy edge strut area"),
                                        modulus_mpa=positive(properties.get("modulus_mpa", 200000.), "Strut modulus"),
                                        kind="longitudinal_strut"))
    # Intermediate end-wall posts are axial-only in this first model. Their
    # out-of-plane end-pressure bending is represented by the roof/base split.
    for face, y in (("front_end", 0.), ("back_end", g.length_mm)):
        for j, x in enumerate(g.end_wall_stations_mm):
            base = node((x, y, 0.), f"{face} ground x={x/1000:g} m", True)
            if face in end_post_faces and 0 < j < len(g.end_wall_stations_mm)-1:
                top = node((x, y, g.roof_z(x)))
                model.add_member(AxialMember(f"post/{face}/{j}", base, top,
                                            positive(end_post_area_mm2, "End-post area"), kind="end_post"))
    for identifier in sorted(layout.pinned):
        panel = layout.panels[identifier]
        kind = brace_group(panel.face)
        area = {"roof_brace": roof_brace_area_mm2, "end_brace": end_brace_area_mm2,
                "wall_brace": wall_brace_area_mm2, "canopy_brace": canopy_brace_area_mm2}[kind]
        if area is None:
            raise AnalysisError("Select a canopy brace section to analyse braced canopy panels")
        for j, (a, b) in enumerate(panel.diagonals):
            start = node(a, f"{identifier}, base", abs(a[2]) < 1e-6)
            end = node(b, f"{identifier}, base", abs(b[2]) < 1e-6)
            model.add_member(AxialMember(f"brace/{identifier}/{j}", start, end,
                                        positive(area, "Brace area"), tension_only=True,
                                        modulus_mpa=positive((brace_moduli or {}).get(kind, 200000.), "Brace modulus"),
                                        kind=kind, panel_id=identifier))
    model.portal_braces = []
    if layout.portal_braces:
        from bracing_portal_brace import PortalBraceError, PortalBraceFrame
        sections = portal_brace_sections or {}
        if sections.get('column') is None or sections.get('beam') is None:
            raise AnalysisError("Select portal brace column and beam sections to analyse portal braces")
        for identifier in sorted(layout.portal_braces, key=lambda p: (p.split('/')[0], int(p.split('/')[2]))):
            face, _, bay = identifier.split('/')
            bay = int(bay)
            line = 0 if face == 'portal_left' else len(g.roof_stations)-1
            a, b = roof_grid[bay][line], roof_grid[bay+1][line]
            height = model.nodes[a][2]
            try:
                frame = PortalBraceFrame(height, g.bay_stations_mm[bay+1]-g.bay_stations_mm[bay],
                                         sections['column'], sections['beam'])
            except PortalBraceError as error:
                raise AnalysisError(str(error)) from None
            dofs = [3*a+1, 3*b+1]
            model.add_block(dofs, frame.stiffness)
            model.portal_braces.append({'id': identifier, 'face': face, 'bay': bay, 'nodes': (a, b), 'dofs': dofs,
                                        'block': len(model.blocks)-1,
                                        'x_mm': model.nodes[a][0], 'y_mm': (model.nodes[a][1], model.nodes[b][1]),
                                        'frame': frame})
    return model, roof_grid, portal_dofs, point_nodes


def end_wall_load_vector(model, layout, point_nodes, front_kpa, back_kpa, pressure_segments=None):
    """Signed +Y net pressures. Integrate vertical-strip roof/base reactions.

    Half the strip resultant goes to its roof and half to ground. Two-point
    Gaussian integration preserves resultant and moment for linear roof slopes.
    Ground interpolation uses real end-post stations, never an invented ridge post.
    """
    g = layout.geometry
    vector = np.zeros(3 * len(model.nodes))
    workings = []
    for face, y, pressure in (("front_end", 0., front_kpa), ("back_end", g.length_mm, back_kpa)):
        if not math.isfinite(float(pressure)):
            raise AnalysisError("End-wall pressure must be finite")
        roof_total = 0.
        ground_total = 0.
        segments = (pressure_segments or {}).get(face,[(0.,g.span_mm,pressure)])
        if (not segments or abs(segments[0][0])>1e-6 or abs(segments[-1][1]-g.span_mm)>1e-6
                or any(len(s)!=3 or not all(math.isfinite(v) for v in s) or s[1]<=s[0] for s in segments)
                or any(abs(a[1]-b[0])>1e-6 for a,b in zip(segments,segments[1:]))):
            raise AnalysisError('End-wall pressure segments must cover the span continuously')
        boundaries=sorted(set(g.roof_stations)|{x for a,b,p in segments for x in (a,b)})
        for lo, hi in zip(boundaries,boundaries[1:]):
            a,b=next((a,b) for a,b in zip(g.roof_stations,g.roof_stations[1:]) if a<=lo+1e-6 and b>=hi-1e-6)
            local_pressure=next(p for start,end,p in segments if start <= (lo+hi)/2 <= end)
            for gauss in (-1/math.sqrt(3), 1/math.sqrt(3)):
                x = (lo+hi)/2 + gauss*(hi-lo)/2
                amount = float(local_pressure) / 1000. * g.roof_z(x) * .5 * (hi-lo)/2
                ratio = (x-a)/(b-a)
                for station, weight in ((a, 1-ratio), (b, ratio)):
                    index = point_nodes[tuple(round(v, 6) for v in (station, y, g.roof_z(station)))]
                    vector[3*index+1] += amount * weight
                    roof_total += amount * weight
                for c, d in zip(g.end_wall_stations_mm, g.end_wall_stations_mm[1:]):
                    if c-1e-6 <= x <= d+1e-6:
                        base_ratio = (x-c)/(d-c)
                        for station, weight in ((c, 1-base_ratio), (d, base_ratio)):
                            index = point_nodes[tuple(round(v, 6) for v in (station, y, 0.))]
                            vector[3*index+1] += amount * weight
                            ground_total += amount * weight
                        break
        workings.append({"face": face, "signed_y_pressure_kpa": pressure,
                         "roof_resultant_n": roof_total, "direct_ground_resultant_n": ground_total,
                         **({'pressure_segments_mm_kpa':[list(s) for s in segments]} if pressure_segments else {})})
    return vector, workings


def roof_drag_load_vector(model, layout, point_nodes, drag):
    """Consistent roof-node loads on the downwind projected roof strip.

    Bilinear shape weights preserve total Y force and its X/Z moments on
    irregular bay/roof grids, including a cutoff part-way through a bay.
    The whole drag force enters the roof; none is applied directly to ground.
    """
    vector = np.zeros(3*len(model.nodes))
    if drag is None:
        return vector, []
    g=layout.geometry
    start,end = float(drag['y_start_m'])*1000,float(drag['y_end_m'])*1000
    pressure=float(drag['signed_y_pressure_kpa'])
    if not all(math.isfinite(v) for v in (start,end,pressure)) or not 0<=start<=end<=g.length_mm+1e-5:
        raise AnalysisError('Invalid roof-friction load strip or pressure')
    for ya,yb in zip(g.bay_stations_mm,g.bay_stations_mm[1:]):
        lo,hi=max(ya,start),min(yb,end)
        if hi<=lo:
            continue
        for xa,xb in zip(g.roof_stations,g.roof_stations[1:]):
            for gx in (-1/math.sqrt(3),1/math.sqrt(3)):
                x=(xa+xb)/2+gx*(xb-xa)/2
                rx=(x-xa)/(xb-xa)
                for gy in (-1/math.sqrt(3),1/math.sqrt(3)):
                    y=(lo+hi)/2+gy*(hi-lo)/2
                    ry=(y-ya)/(yb-ya)
                    force=pressure/1000*(xb-xa)/2*(hi-lo)/2
                    for sx,wx in ((xa,1-rx),(xb,rx)):
                        for sy,wy in ((ya,1-ry),(yb,ry)):
                            index=point_nodes[tuple(round(v,6) for v in (sx,sy,g.roof_z(sx)))]
                            vector[3*index+1]+=force*wx*wy
    workings=[{'face':'roof_friction','signed_y_pressure_kpa':pressure,
               'roof_resultant_n':float(vector[1::3].sum()),'direct_ground_resultant_n':0.,
               'y_start_m':start/1000,'y_end_m':end/1000}]
    # Open canopy friction (top and underside) on each canopy's own strip,
    # shared between the main eave line and the canopy edge nodes.
    for canopy in drag.get('canopies',()):
        side=canopy['side']
        if g.canopy(side) is None:
            raise AnalysisError(f'Canopy friction was given for a {side} canopy that is not in the bracing layout')
        part=np.zeros(3*len(model.nodes))
        c_start,c_end=float(canopy['y_start_m'])*1000,float(canopy['y_end_m'])*1000
        c_pressure=float(canopy['signed_y_pressure_kpa'])
        if not all(math.isfinite(v) for v in (c_start,c_end,c_pressure)) or not 0<=c_start<=c_end<=g.length_mm+1e-5:
            raise AnalysisError('Invalid canopy-friction load strip or pressure')
        root=g.canopy_root_x(side);tip,tip_z=g.canopy_tip(side)
        height={root:g.roof_z(root),tip:tip_z}
        xa,xb=min(root,tip),max(root,tip)
        for ya,yb in zip(g.bay_stations_mm,g.bay_stations_mm[1:]):
            lo,hi=max(ya,c_start),min(yb,c_end)
            if hi<=lo:
                continue
            for gx in (-1/math.sqrt(3),1/math.sqrt(3)):
                rx=.5+gx/2
                for gy in (-1/math.sqrt(3),1/math.sqrt(3)):
                    y=(lo+hi)/2+gy*(hi-lo)/2
                    ry=(y-ya)/(yb-ya)
                    force=c_pressure/1000*(xb-xa)/2*(hi-lo)/2
                    for sx,wx in ((xa,1-rx),(xb,rx)):
                        for sy,wy in ((ya,1-ry),(yb,ry)):
                            index=point_nodes[tuple(round(v,6) for v in (sx,sy,height[sx]))]
                            part[3*index+1]+=force*wx*wy
        vector+=part
        workings.append({'face':f'{side}_canopy_friction','signed_y_pressure_kpa':c_pressure,
                         'roof_resultant_n':float(part[1::3].sum()),'direct_ground_resultant_n':0.,
                         'y_start_m':c_start/1000,'y_end_m':c_end/1000})
    return vector,workings


def load_path_evidence(nodes, result, workings, portal_braces=()):
    """Resolve actual member forces at ground, independently of group naming.

    X is across the portals, Y is along the building and Z is vertical. End
    braces have no Y projection. Include direct end-post base load separately
    so the ground-applied half of end-wall pressure is not counted as bracing.
    Portal frame brace base reactions are reported under their face.
    """
    peaks, ground, diaphragm_ground = {}, {}, {}
    for member in result['members']:
        kind = member['kind']
        if kind.endswith('brace'):
            peaks[kind] = max(peaks.get(kind,0.),max(0.,member['axial_n']))
        a,b = (np.asarray(nodes[member[key]],dtype=float) for key in ('start','end'))
        direction = (b-a)/np.linalg.norm(b-a)
        vector = member['axial_n']*direction
        for point,sign in ((a,-1.),(b,1.)):
            if abs(point[2])<1e-6:
                face = member.get('panel_id','').split('/')[0] or kind
                if kind == 'wall_diaphragm':
                    face = member['panel_id'].split('/')[1]
                    diaphragm_ground.setdefault(face,np.zeros(3))
                    diaphragm_ground[face] += sign*vector
                ground.setdefault(face,np.zeros(3))
                ground[face] += sign*vector
    for brace in portal_braces:
        ground.setdefault(brace['face'],np.zeros(3))
        for base in brace['base_reactions_n']:
            ground[brace['face']] += np.array([0.,base['fy_n'],base['fz_n']])
    roof_y = sum(w['roof_resultant_n'] for w in workings)
    direct_y = sum(w['direct_ground_resultant_n'] for w in workings)
    member_y = sum(v[1] for v in ground.values())
    return {'axes':'X across span; Y front to back along length; Z vertical',
            'applied_roof_y_n':roof_y,'direct_ground_y_n':direct_y,
            'member_base_reactions_n':{face:v.tolist() for face,v in ground.items()},
            'roof_to_member_base_y_residual_n':float(roof_y+member_y),
            'peak_brace_tension_n':peaks,
            **({'diaphragm_base_reactions_n':{face:v.tolist() for face,v in diaphragm_ground.items()}} if diaphragm_ground else {}),
            'basis':'End-wall normal pressure acts along Y, including the varying front/back suction under transverse wind. Roof/struts transfer roof-applied Y forces to side-wall bracing and any enabled SLS side-wall diaphragms. Normal roof/side-wall pressures also act through the portals, producing transverse X/Z reactions and coupled tie/brace forces. Front/back end-wall braces lie in X/Z: they can resist transverse roof/portal actions but have zero direct Y resistance. ULS transverse cases use conventional bracing only.'}


def solve_building_case(model, layout, portal, portal_dofs, point_nodes, case, member_forces=True):
    loads, workings = end_wall_load_vector(model, layout, point_nodes, case["front_kpa"], case["back_kpa"],case.get('end_wall_pressure_segments'))
    drag_loads,drag_workings=roof_drag_load_vector(model,layout,point_nodes,case.get('roof_drag'))
    loads+=drag_loads
    workings+=drag_workings
    network_loads = loads.copy()
    portal_states = case.get('portal_loads')
    if portal_states is not None and len(portal_states) != len(portal_dofs):
        raise AnalysisError('Each frame line needs its own portal load vector')
    loaded_portals = []
    for index, dofs in enumerate(portal_dofs):
        state = portal_states[index] if portal_states is not None else None
        loaded = portal.with_loads(state['loads_n'], state.get('member_equivalent_loads')) if state else portal
        loaded_portals.append(loaded)
        loads[dofs] += loaded.equivalent_load
    result = model.solve(loads, allow_neutral_equilibrium=case.get('gravity_only',False))
    displacements = np.asarray(result["displacements_mm"]).reshape(-1)
    portals = []
    support_forces = np.array([item["force_n"] for item in result["support_reactions_n"]], dtype=float).reshape((-1, 3))
    support_points = np.array([item["point_mm"] for item in result["support_reactions_n"]], dtype=float).reshape((-1, 3))
    reactions = support_forces.sum(axis=0)
    moments = np.cross(support_points, support_forces).sum(axis=0)
    # Portal native coordinates may have an X origin offset. Transform every
    # reaction and load into the same building coordinate system. In-plane
    # (x, z) forces at (x, y, z): moment = (y*fz, z*fx - x*fz, -y*fx) plus
    # the in-plane moment about -y.
    x0 = portal.points[0][0]
    for index, dofs in enumerate(portal_dofs):
        loaded = loaded_portals[index]
        recovered = loaded.recover(displacements[dofs], member_forces=member_forces)
        y = layout.geometry.bay_stations_mm[index]
        supports = recovered["support_reactions"]
        if supports:
            fx = np.array([item["fx_n"] for item in supports])
            fz = np.array([item["fz_n"] for item in supports])
            x = np.array([item["x_mm"] for item in supports]) - x0
            z = np.array([item["z_mm"] for item in supports])
            reactions += (fx.sum(), 0., fz.sum())
            moments += (y*fz.sum(), np.dot(z, fx)-np.dot(x, fz)-sum(item["m_xz_nmm"] for item in supports), -y*fx.sum())
        portals.append({"index": index, "y_mm": y, **recovered})
    # Portal frame braces: unloaded condensed wall frames. Their pinned-base
    # reactions (Y along the building, Z vertical) at (x, y, 0) enter the
    # balance like the portal supports. Member forces are always recovered:
    # the brace design checks need them.
    portal_braces = []
    for brace in getattr(model, 'portal_braces', ()):
        recovered = brace['frame'].recover(displacements[brace['dofs']])
        for base, y in zip(recovered['base_reactions_n'], brace['y_mm']):
            fy, fz = base['fy_n'], base['fz_n']
            base['point_mm'] = [brace['x_mm'], y, 0.]
            reactions += (0., fy, fz)
            moments += (y*fz, -brace['x_mm']*fz, brace['x_mm']*fy)
        portal_braces.append({'id': brace['id'], 'face': brace['face'], 'bay': brace['bay'],
                              'eave_y_mm': displacements[brace['dofs']].tolist(), **recovered})
    # Condensed portal loads are equivalent work at the interfaces, not the
    # physical applied loads. Use original portal nodal forces/moments for
    # global balance, including loads applied directly at native supports.
    external = network_loads.reshape((-1, 3))
    external_moment = np.sum(np.cross(np.asarray(model.nodes), external), axis=0)
    applied_force = external.sum(axis=0)
    full_load_scale = 0.
    portal_workings = []
    nodes = portal.structure.nodes
    native_ux = np.array([n.native_ux for n in nodes], dtype=int)
    native_uy = np.array([n.native_uy for n in nodes], dtype=int)
    native_rz = np.array([n.native_rz for n in nodes], dtype=int)
    node_x = np.array([n.x for n in nodes], dtype=float) - x0
    node_z = np.array([n.y for n in nodes], dtype=float)
    for index, loaded in enumerate(loaded_portals):
        if loaded.structure is not portal.structure:
            raise AnalysisError('Each loaded portal must share the building portal structure')
        vector = loaded.loads
        y = layout.geometry.bay_stations_mm[index]
        fx, fz = vector[native_ux], vector[native_uy]
        force = np.array([fx.sum(), 0., fz.sum()])
        moment = np.array([y*fz.sum(), np.dot(node_z, fx)-np.dot(node_x, fz)-vector[native_rz].sum(), -y*fx.sum()])
        full_load_scale += float(np.hypot(fx, fz).sum())
        applied_force += force
        external_moment += moment
        if portal_states is not None:
            portal_workings.append({'index': index, 'y_mm': layout.geometry.bay_stations_mm[index],
                'applied_force_n': force.tolist(), 'applied_moment_nmm': moment.tolist()})
    force_error = reactions + applied_force
    moment_error = moments + external_moment
    force_scale = max(1., float(np.sum(np.linalg.norm(external, axis=1)))+full_load_scale)
    length_scale = max(layout.geometry.span_mm, layout.geometry.length_mm, layout.geometry.eave_mm)
    if np.max(np.abs(force_error)) > force_scale*1e-6 or np.max(np.abs(moment_error)) > force_scale*length_scale*1e-6:
        raise AnalysisError("Recovered support reactions failed the building force/moment balance check")
    return {"case": case, "load_workings": workings, "nodal_loads_n": loads.reshape((-1,3)).tolist(),
            'network_nodal_loads_n': external.tolist(), 'portal_load_workings': portal_workings,
            'applied_resultant_n': applied_force.tolist(),
            'load_path':load_path_evidence(model.nodes,result,workings,portal_braces),
            **result, "portals": portals, **({"portal_braces": portal_braces} if portal_braces else {}),
            "balance_force_error_n": force_error.tolist(), "balance_moment_error_nmm": moment_error.tolist()}
