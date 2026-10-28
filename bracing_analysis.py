"""Small-displacement 3D axial network with elastic portal interface blocks.

Units: N, mm, MPa. Rods use a unilateral elastic law, N = EA/L max(extension, 0).
No penalty supports, compression stiffness in slack rods, sizing or code checks.
"""
from dataclasses import dataclass, replace
import math

import numpy as np


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
    diagonal = np.diag(matrix)
    if not len(diagonal) or np.any(diagonal <= 0) or not np.all(np.isfinite(matrix)):
        raise AnalysisError("Unstable model: a free movement has no stiffness")
    scale = np.sqrt(diagonal)
    scaled = matrix / scale[:, None] / scale[None, :]
    try:
        factor = np.linalg.cholesky((scaled + scaled.T) * .5)
        if np.min(np.diag(factor)) < 1e-6:
            raise np.linalg.LinAlgError("near mechanism")
        rhs = np.asarray(force) / (scale if np.ndim(force) == 1 else scale[:, None])
        answer = np.linalg.solve(factor.T, np.linalg.solve(factor, rhs))
    except np.linalg.LinAlgError:
        raise AnalysisError("Unstable or ill-conditioned model: check brace continuity and supports") from None
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

    def solve(self, loads, max_iterations=160):
        size = 3 * len(self.nodes)
        force = np.asarray(loads, dtype=float)
        if force.shape != (size,) or not np.all(np.isfinite(force)):
            raise AnalysisError("A load case needs one finite load per translational DOF")
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
        f = force[free]
        # The all-rods system is an upper stiffness bound. If it is unstable,
        # removing compressive rods cannot produce a stable network.
        initial = elastic_solve(full, f)
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
            tangent = base + rod_rows[active].T @ (rod_ks[active, None] * rod_rows[active])
            try:
                direction = initial if iteration == 1 else elastic_solve(tangent, -gradient)
            except AnalysisError:
                # A search direction only: the physical force law and final
                # equilibrium always use zero compression force in slack rods.
                direction = elastic_solve(full, -gradient)
            slope = float(gradient @ direction)
            if not math.isfinite(slope) or slope >= 0:
                raise AnalysisError("Tension-only analysis could not find an equilibrium direction")
            step = 1.
            for _ in range(45):
                candidate = u + step * direction
                if state(candidate)[3] <= energy + 1e-4 * step * slope + 1e-12:
                    break
                step *= .5
            else:
                raise AnalysisError("Tension-only analysis did not converge")
            u = candidate
        if not converged:
            raise AnalysisError("Tension-only analysis did not converge; layout may have a mechanism")
        extension, tension, gradient, _ = state(u)
        active = extension >= -1e-10
        tangent = base + rod_rows[active].T @ (rod_ks[active, None] * rod_rows[active])
        elastic_solve(tangent, np.zeros(len(free)))
        # A zero-force rod has only one-sided tangent stiffness. Do not present
        # equilibrium of a neutrally loaded direction as verified stability.
        taut = extension > 1e-10
        try:
            elastic_solve(base + rod_rows[taut].T @ (rod_ks[taut, None] * rod_rows[taut]), np.zeros(len(free)))
            tangent_status = "positive_with_taut_rods"
        except AnalysisError:
            tangent_status = "neutral_rods_required_stability_unverified"
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
        }


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
        top = [element for element in structure.elements if element.group == "TOP"]
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
                raise AnalysisError(f"Portal roof attachment {point} does not lie on a TOP element")
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
        fi = internal.T @ self.loads[self.free]
        if internal.shape[1]:
            solved = elastic_solve(kii, np.column_stack((kiu, fi)))
            self.recovery = prescribed - internal @ solved[:, :-1]
            self.particular = internal @ solved[:, -1]
            self.stiffness = prescribed.T @ kff @ prescribed - kiu.T @ solved[:, :-1]
            self.equivalent_load = prescribed.T @ self.loads[self.free] - kiu.T @ solved[:, -1]
        else:
            self.recovery = prescribed
            self.particular = np.zeros(len(self.free))
            self.stiffness = prescribed.T @ kff @ prescribed
            self.equivalent_load = prescribed.T @ self.loads[self.free]
        self.stiffness = (self.stiffness + self.stiffness.T) * .5

    def recover(self, translations):
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
        return {
            "interface_forces_n": interface_force.reshape((-1, 2)).tolist(),
            "interior_attachment_point_loads": point_loads,
            "support_reactions": supports,
            "node_displacements": [{"node": n.id, "x_mm": n.x, "z_mm": n.y,
                                    "ux_mm": float(d[n.ux]), "uz_mm": float(d[n.uy]),
                                    "rotation_xz_rad": float(d[n.rz])} for n in self.structure.nodes],
            "member_end_forces": [{"element": e.id, "group": e.group, "section": e.profile.name,
                                   "local_n_n_nmm": (e.local_end_forces(d)-member_point_loads.get(e.id, np.zeros(6))).tolist()}
                                  for e in self.structure.elements],
        }


def build_braced_building(layout, portal, strut_area_mm2, roof_brace_area_mm2,
                         wall_brace_area_mm2, end_brace_area_mm2, end_post_area_mm2,
                         end_post_faces=("front_end", "back_end")):
    if not layout.pinned:
        raise AnalysisError("Pin the brace panels to analyse in the 3D location editor first. Available panels are not installed braces.")
    if np.max(np.abs(portal.equivalent_load), initial=0) > 1e-8:
        raise AnalysisError("This building-analysis stage requires an unloaded portal macro")
    g = layout.geometry
    if len(g.roof_stations) * len(g.bay_stations_mm) * 3 > 600:
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
        dofs = [dof for index in roof for dof in (3*index, 3*index+2)]
        model.add_block(dofs, portal.stiffness)
        roof_grid.append(roof)
        portal_dofs.append(dofs)
    for i, (y0, y1) in enumerate(zip(g.bay_stations_mm, g.bay_stations_mm[1:])):
        for j, x in enumerate(g.roof_stations):
            model.add_member(AxialMember(f"strut/bay/{i}/line/{j}", roof_grid[i][j], roof_grid[i+1][j],
                                        positive(strut_area_mm2, "Strut area"), kind="longitudinal_strut"))
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
        is_roof = panel.face.startswith("roof")
        is_end = panel.face in {"front_end", "back_end"}
        area = roof_brace_area_mm2 if is_roof else end_brace_area_mm2 if is_end else wall_brace_area_mm2
        for j, (a, b) in enumerate(panel.diagonals):
            start = node(a, f"{identifier}, base", abs(a[2]) < 1e-6)
            end = node(b, f"{identifier}, base", abs(b[2]) < 1e-6)
            model.add_member(AxialMember(f"brace/{identifier}/{j}", start, end,
                                        positive(area, "Brace area"), tension_only=True,
                                        kind="roof_brace" if is_roof else "end_brace" if is_end else "wall_brace",
                                        panel_id=identifier))
    return model, roof_grid, portal_dofs, point_nodes


def end_wall_load_vector(model, layout, point_nodes, front_kpa, back_kpa):
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
        for a, b in zip(g.roof_stations, g.roof_stations[1:]):
            for gauss in (-1/math.sqrt(3), 1/math.sqrt(3)):
                x = (a+b)/2 + gauss*(b-a)/2
                amount = float(pressure) / 1000. * g.roof_z(x) * .5 * (b-a)/2
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
                         "roof_resultant_n": roof_total, "direct_ground_resultant_n": ground_total})
    return vector, workings


def solve_building_case(model, layout, portal, portal_dofs, point_nodes, case):
    loads, workings = end_wall_load_vector(model, layout, point_nodes, case["front_kpa"], case["back_kpa"])
    result = model.solve(loads)
    displacements = np.asarray(result["displacements_mm"]).reshape(-1)
    portals = []
    reactions = np.zeros(3)
    moments = np.zeros(3)
    for item in result["support_reactions_n"]:
        value = np.asarray(item["force_n"])
        reactions += value
        moments += np.cross(item["point_mm"], value)
    for index, dofs in enumerate(portal_dofs):
        recovered = portal.recover(displacements[dofs])
        y = layout.geometry.bay_stations_mm[index]
        for item in recovered["support_reactions"]:
            value = np.array([item["fx_n"], 0., item["fz_n"]])
            reactions += value
            # Portal native coordinates may have an X origin offset. Transform
            # every reaction into the same building coordinate system.
            point = (item["x_mm"]-portal.points[0][0], y, item["z_mm"])
            moments += np.cross(point, value) + np.array([0., -item["m_xz_nmm"], 0.])
        portals.append({"index": index, "y_mm": y, **recovered})
    external = loads.reshape((-1, 3))
    external_moment = np.sum(np.cross(np.asarray(model.nodes), external), axis=0)
    force_error = reactions + external.sum(axis=0)
    moment_error = moments + external_moment
    force_scale = max(1., float(np.sum(np.linalg.norm(external, axis=1))))
    length_scale = max(layout.geometry.span_mm, layout.geometry.length_mm, layout.geometry.eave_mm)
    if np.max(np.abs(force_error)) > force_scale*1e-6 or np.max(np.abs(moment_error)) > force_scale*length_scale*1e-6:
        raise AnalysisError("Recovered support reactions failed the building force/moment balance check")
    return {"case": case, "load_workings": workings, "nodal_loads_n": external.tolist(),
            **result, "portals": portals,
            "balance_force_error_n": force_error.tolist(), "balance_moment_error_nmm": moment_error.tolist()}
