"""Portal frame braces: UB moment frames in side-wall bays.

A portal brace sits in one side-wall bay between two portal frames: a UB column
stitch-welded to the inside of each portal column and a UB beam moment-connected
across the top at eave level. It resists longitudinal (Y) movement of the eave
line where X bracing cannot go (an open or blocked bay).

Model (stated in reports):

- A 2D Euler-Bernoulli frame in the wall's Y-Z plane. The columns bend about
  their strong axis along the building, with pinned bases on their own base
  plates. The knees are rigid, and the beam spans the bay at eave height.
- Members lie on the portal column line; the stitch-weld offset and the portal
  columns' own weak-axis stiffness are ignored.
- It is statically condensed onto the Y translations of the two eave nodes.
  The columns carry their own axial forces to their own bases (no vertical
  coupling with the portal), and the rotations and vertical movements are
  internal.
- No loads act on the frame itself (an open or blocked bay has no cladding
  load on the brace); self-weight is neglected.
"""
from dataclasses import dataclass

import numpy as np

E_MPA = 200000.


class PortalBraceError(ValueError):
    pass


@dataclass(frozen=True)
class Section:
    name: str
    area_mm2: float
    inertia_mm4: float   # strong-axis second moment of area


PORTAL_BRACE_GROUPS = ('portal_brace_column', 'portal_brace_beam')
MEMBER_GROUP = {'column_a': 'portal_brace_column', 'column_b': 'portal_brace_column', 'beam': 'portal_brace_beam'}


class PortalBraceCandidate:
    """A catalogue UB offered to a portal brace group.

    Delegates to the catalogue profile (name, A, check_properties, costing
    fields). Its sizing stiffness is the strong-axis E I, because a portal
    brace resists sway in bending, not axially.
    """

    def __init__(self, profile):
        self.profile = profile

    def __getattr__(self, item):
        if item == 'profile':
            raise AttributeError(item)
        return getattr(self.profile, item)

    def __getstate__(self):
        return {'profile': self.profile}

    def __setstate__(self, state):
        self.profile = state['profile']

    def __eq__(self, other):
        return isinstance(other, PortalBraceCandidate) and other.profile.name == self.profile.name

    def __hash__(self):
        return hash(('portal_brace', self.profile.name))

    def __repr__(self):
        return f'PortalBraceCandidate({self.profile.name!r})'

    @property
    def bending_stiffness(self):
        return E_MPA*float(self.profile.I_analysis)


def portal_candidates(profiles):
    """Portal brace candidates from catalogue UBs, lightest first."""
    return [PortalBraceCandidate(p) for p in sorted(profiles, key=lambda p: (p.A, p.name))]


def section_of(profile):
    """A Section from a catalogue profile (``A``, ``I_analysis``) or a dict."""
    if isinstance(profile, Section):
        return profile
    if isinstance(profile, PortalBraceCandidate):
        profile = profile.profile
    if isinstance(profile, dict):
        return Section(profile.get('label') or profile.get('name'), float(profile['area_mm2']), float(profile['inertia_mm4']))
    inertia = float(getattr(profile, 'I_analysis', 0.) or max(getattr(profile, 'Iyp', 0.), getattr(profile, 'Izp', 0.)))
    return Section(profile.name, float(profile.A), inertia)


def _element(length, area, inertia, cos, sin):
    """Global 6x6 stiffness and the local transformation of a 2D frame element."""
    ea, ei = E_MPA*area/length, E_MPA*inertia
    k = np.array([
        [ea, 0, 0, -ea, 0, 0],
        [0, 12*ei/length**3, 6*ei/length**2, 0, -12*ei/length**3, 6*ei/length**2],
        [0, 6*ei/length**2, 4*ei/length, 0, -6*ei/length**2, 2*ei/length],
        [-ea, 0, 0, ea, 0, 0],
        [0, -12*ei/length**3, -6*ei/length**2, 0, 12*ei/length**3, -6*ei/length**2],
        [0, 6*ei/length**2, 2*ei/length, 0, -6*ei/length**2, 4*ei/length]])
    t = np.zeros((6, 6))
    for i in (0, 3):
        t[i:i+2, i:i+2] = [[cos, sin], [-sin, cos]]
        t[i+2, i+2] = 1.
    return t.T @ k @ t, k, t


class PortalBraceFrame:
    """One portal brace, condensed onto the two eave Y translations.

    Local 2D axes: x along the building from the bay's first frame line,
    y vertical. Nodes: 0 base A, 1 knee A (eave), 2 knee B (eave), 3 base B.
    ``stiffness`` is the 2x2 condensed matrix for [u_A, u_B] (N/mm).
    """

    MEMBERS = (('column_a', 0, 1), ('beam', 1, 2), ('column_b', 3, 2))

    def __init__(self, height_mm, bay_mm, column, beam):
        self.height_mm, self.bay_mm = float(height_mm), float(bay_mm)
        if not (self.height_mm > 0 and self.bay_mm > 0):
            raise PortalBraceError('A portal brace needs a positive height and bay length')
        self.column, self.beam = section_of(column), section_of(beam)
        for section in (self.column, self.beam):
            if not (section.area_mm2 > 0 and section.inertia_mm4 > 0):
                raise PortalBraceError(f'Portal brace section {section.name} needs a positive area and inertia')
        self.points = np.array([[0., 0.], [0., self.height_mm], [self.bay_mm, self.height_mm], [self.bay_mm, 0.]])
        size = 12
        self.k_full = np.zeros((size, size))
        self.elements = []
        for name, a, b in self.MEMBERS:
            section = self.beam if name == 'beam' else self.column
            delta = self.points[b]-self.points[a]
            length = float(np.hypot(*delta))
            kg, kl, t = _element(length, section.area_mm2, section.inertia_mm4, delta[0]/length, delta[1]/length)
            dofs = [3*a, 3*a+1, 3*a+2, 3*b, 3*b+1, 3*b+2]
            self.k_full[np.ix_(dofs, dofs)] += kg
            self.elements.append({'name': name, 'section': section, 'length_mm': length, 'dofs': dofs, 'k_local': kl, 't': t})
        # Pinned bases: x and y fixed at nodes 0 and 3; base rotations are free.
        self.restrained = [0, 1, 9, 10]
        self.attached = [3, 6]   # u at the two knees (eave nodes A and B)
        self.internal = [d for d in range(size) if d not in self.restrained and d not in self.attached]
        kaa = self.k_full[np.ix_(self.attached, self.attached)]
        kai = self.k_full[np.ix_(self.attached, self.internal)]
        kii = self.k_full[np.ix_(self.internal, self.internal)]
        try:
            self._kii_kia = np.linalg.solve(kii, kai.T)
        except np.linalg.LinAlgError:
            raise PortalBraceError('Portal brace frame is unstable with its attachments held') from None
        stiffness = kaa-kai @ self._kii_kia
        self.stiffness = (stiffness+stiffness.T)*.5

    def displacements(self, translations):
        """Full 12-DOF displacement vector for eave Y translations [u_A, u_B]."""
        u = np.asarray(translations, dtype=float).reshape(2)
        d = np.zeros(12)
        d[self.attached] = u
        d[self.internal] = -self._kii_kia @ u
        return d

    def recover(self, translations):
        """Interface forces, base reactions and member end forces for [u_A, u_B] (mm).

        Member end forces are local [N1, V1, M1, N2, V2, M2] (N, N mm), with N
        positive in tension at end 2 (the usual stiffness sign convention).
        """
        d = self.displacements(translations)
        forces = self.k_full @ d
        members = []
        for element in self.elements:
            local = element['k_local'] @ (element['t'] @ d[element['dofs']])
            n = float(local[3])   # +ve tension
            members.append({'member': element['name'], 'section': element['section'].name,
                            'length_mm': element['length_mm'], 'local_n_n_nmm': local.tolist(),
                            'axial_n': n, 'shear_n': float(max(abs(local[1]), abs(local[4]))),
                            'moment_nmm': float(max(abs(local[2]), abs(local[5])))})
        return {'interface_forces_n': forces[self.attached].tolist(),
                'base_reactions_n': [{'base': 'A', 'fy_n': float(forces[0]), 'fz_n': float(forces[1])},
                                     {'base': 'B', 'fy_n': float(forces[9]), 'fz_n': float(forces[10])}],
                'members': members}


def scaled_frame(frame, group, ratio):
    """The same portal brace with its column or beam strong-axis inertia scaled."""
    scale = lambda s: Section(s.name, s.area_mm2, s.inertia_mm4*ratio)
    column = scale(frame.column) if group == 'portal_brace_column' else frame.column
    beam = scale(frame.beam) if group == 'portal_brace_beam' else frame.beam
    return PortalBraceFrame(frame.height_mm, frame.bay_mm, column, beam)


def closed_form_stiffness(height_mm, bay_mm, column_inertia, beam_inertia):
    """Total lateral stiffness of a pinned-base portal (equal columns, axially rigid members).

    Antisymmetric sway under a total knee load P: each column takes P/2, the
    knee moment is P h/2 and the beam (double curvature) rotates M L/(6 E Ib),
    so the sway is P h^2 (L/Ib + 2h/Ic)/(12 E) and
        K = 12 E Ic Ib / (h^2 (Ic L + 2 Ib h)).
    A rigid beam gives 6 E Ic/h^3: two pinned-base cantilevers.
    """
    return 12*E_MPA*column_inertia*beam_inertia/(height_mm**2*(column_inertia*bay_mm+2*beam_inertia*height_mm))
