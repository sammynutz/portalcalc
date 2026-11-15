"""Steel section properties and the section catalogue (Shape_Catalog.xlsx)."""

import math
import re
from dataclasses import dataclass

from openpyxl import load_workbook

from portalcalc.frame.common import clean_profile_label, profile_label_with_weight
from portalcalc.member_checks import SectionCheckProperties


# ==========================================================
# PROFILE CATALOG
# ==========================================================

@dataclass
class SectionProfile:
    name: str
    group_name: str
    shape_type: str
    A: float       # mm^2
    J: float       # mm^4
    Iyp: float     # mm^4
    Izp: float     # mm^4
    Iw: float      # mm^6
    Syp: float = 0.0
    Szp: float = 0.0
    fabrication: str = ""
    fy: float = 300.0
    fyw: float = 300.0
    fu: float = 440.0
    depth: float = 0.0
    flange_width: float = 0.0
    flange_thickness: float = 0.0
    web_thickness: float = 0.0

    @property
    def I_analysis(self):
        """
        Temporary 2D analysis inertia assumption.
        For a final design tool, this should become a selectable strong/weak-axis setting.
        """
        return max(self.Iyp, self.Izp)

    @property
    def display_name(self):
        kg_m = self.A * 1e-6 * 7850.0
        return profile_label_with_weight(self.name, kg_m)

    def check_properties(self):
        return SectionCheckProperties(
            name=self.name,
            shape_type=self.shape_type,
            fabrication=self.fabrication,
            area_mm2=self.A,
            j_mm4=self.J,
            iy_mm4=self.Iyp,
            iz_mm4=self.Izp,
            iw_mm6=self.Iw,
            sy_mm3=self.Syp or None,
            sz_mm3=self.Szp or None,
            fy_mpa=self.fy,
            fyw_mpa=self.fyw,
            fu_mpa=self.fu,
            depth_mm=self.depth or None,
            flange_width_mm=self.flange_width or None,
            flange_thickness_mm=self.flange_thickness or None,
            web_thickness_mm=self.web_thickness or None,
        )


STRUCTURAL_CHS_C350L0_SIZES = (
    (60.3, 5.5),
    (76.1, 4.0),
    (76.1, 5.0),
    (88.9, 4.0),
    (88.9, 5.5),
    (88.9, 6.0),
    (101.6, 6.0),
    (114.3, 4.8),
    (114.3, 5.4),
    (114.3, 6.0),
    (139.7, 5.4),
    (165.1, 3.2),
    (168.3, 3.2),
    (168.3, 4.8),
    (168.3, 6.4),
    (219.1, 4.8),
    (219.1, 6.4),
    (273.1, 4.8),
    (273.1, 6.4),
    (273.1, 9.3),
    (273.1, 12.7),
    (323.9, 6.4),
    (323.9, 9.5),
    (323.9, 12.7),
    (355.6, 6.4),
    (355.6, 9.5),
    (355.6, 12.7),
    (406.4, 6.4),
    (406.4, 9.5),
    (406.4, 12.7),
)


def structural_chs_profile(diameter_mm, thickness_mm):
    """Build nominal section properties for an AS/NZS 1163 C350L0 CHS."""
    diameter_mm = float(diameter_mm)
    thickness_mm = float(thickness_mm)
    inner_diameter_mm = diameter_mm - 2.0 * thickness_mm
    if diameter_mm <= 0.0 or thickness_mm <= 0.0 or inner_diameter_mm <= 0.0:
        raise ValueError("CHS diameter and thickness must define a hollow circular section.")

    area_mm2 = math.pi * (diameter_mm**2 - inner_diameter_mm**2) / 4.0
    second_moment_mm4 = math.pi * (diameter_mm**4 - inner_diameter_mm**4) / 64.0
    polar_moment_mm4 = 2.0 * second_moment_mm4
    plastic_modulus_mm3 = (diameter_mm**3 - inner_diameter_mm**3) / 6.0
    diameter_label = f"{diameter_mm:g}"
    thickness_label = f"{thickness_mm:g}"
    return SectionProfile(
        name=f"{diameter_label}*{thickness_label} CHS",
        group_name="CHS",
        shape_type="Circular Hollow Section",
        A=area_mm2,
        J=polar_moment_mm4,
        Iyp=second_moment_mm4,
        Izp=second_moment_mm4,
        Iw=0.0,
        Syp=plastic_modulus_mm3,
        Szp=plastic_modulus_mm3,
        fabrication="Cold-formed",
        fy=350.0,
        fyw=350.0,
        fu=430.0,
        depth=diameter_mm,
        flange_width=diameter_mm,
        flange_thickness=thickness_mm,
        web_thickness=thickness_mm,
    )


@dataclass
class PurlinLayout:
    points: list
    max_spacing_mm: float
    end_spacing_limit_mm: float
    mid_spacing_limit_mm: float


class ShapeCatalog:

    def __init__(self, xlsx_path):
        self.xlsx_path = xlsx_path
        self.profiles = []
        self.profile_by_name = {}
        self.load()

    def load(self):
        wb = load_workbook(self.xlsx_path, data_only=True)
        ws = wb.active

        header_row = None
        headers = {}

        for row in ws.iter_rows(min_row=1, max_row=20):
            values = [cell.value for cell in row]
            if "Name" in values and "A" in values and "Iyp" in values and "Izp" in values:
                header_row = row[0].row
                headers = {str(cell.value).strip(): cell.column for cell in row if cell.value is not None}
                break

        if header_row is None:
            raise ValueError("Could not find catalog header row containing Name, A, Iyp and Izp.")

        name_col = headers.get("Name")
        group_col = headers.get("Group Name")
        shape_type_col = headers.get("Shape Type")
        area_col = headers.get("A")
        j_col = headers.get("J")
        iyp_col = headers.get("Iyp")
        izp_col = headers.get("Izp")
        iw_col = headers.get("Iw")
        syp_col = headers.get("Syp")
        szp_col = headers.get("Szp")
        fabrication_col = headers.get("Fabrication")
        depth_col = headers.get("D")
        bt_col = headers.get("Bt")
        bb_col = headers.get("Bb")
        tt_col = headers.get("Tt")
        tb_col = headers.get("Tb")
        tw_col = headers.get("Tw")

        def header_col(*tokens):
            for label, column in headers.items():
                normalized = str(label).replace("_x000D_", " ").replace("\n", " ").lower()
                if all(token.lower() in normalized for token in tokens):
                    return column
            return None

        fy_col = header_col("fy", "normal")
        fyw_col = header_col("fyw", "normal")
        fu_col = header_col("fu", "normal")

        def cell_float(row_number, column, default=0.0):
            if column is None:
                return default
            value = ws.cell(row=row_number, column=column).value
            if value is None or value == "":
                return default
            return float(value)

        def cell_text(row_number, column, default=""):
            if column is None:
                return default
            value = ws.cell(row=row_number, column=column).value
            return str(value or default).strip()

        for row in range(header_row + 2, ws.max_row + 1):
            name = ws.cell(row=row, column=name_col).value
            if not name:
                continue

            A = ws.cell(row=row, column=area_col).value
            Iyp = ws.cell(row=row, column=iyp_col).value
            Izp = ws.cell(row=row, column=izp_col).value

            if A is None or Iyp is None or Izp is None:
                continue

            try:
                profile = SectionProfile(
                    name=str(name).strip(),
                    group_name=str(ws.cell(row=row, column=group_col).value or "").strip(),
                    shape_type=str(ws.cell(row=row, column=shape_type_col).value or "").strip(),
                    A=float(A),
                    J=cell_float(row, j_col),
                    Iyp=float(Iyp),
                    Izp=float(Izp),
                    Iw=cell_float(row, iw_col),
                    Syp=cell_float(row, syp_col),
                    Szp=cell_float(row, szp_col),
                    fabrication=cell_text(row, fabrication_col),
                    fy=cell_float(row, fy_col, 300.0),
                    fyw=cell_float(row, fyw_col) or cell_float(row, fy_col, 300.0),
                    fu=cell_float(row, fu_col, 440.0),
                    depth=cell_float(row, depth_col),
                    flange_width=max(cell_float(row, bt_col), cell_float(row, bb_col)),
                    flange_thickness=max(cell_float(row, tt_col), cell_float(row, tb_col)),
                    web_thickness=cell_float(row, tw_col),
                )
            except (TypeError, ValueError):
                continue

            self.profiles.append(profile)
            self.profile_by_name[profile.name] = profile

        self.add_structural_chs_profiles()
        self.profiles.sort(key=self.profile_sort_key)

    def add_structural_chs_profiles(self):
        for diameter_mm, thickness_mm in STRUCTURAL_CHS_C350L0_SIZES:
            profile = structural_chs_profile(diameter_mm, thickness_mm)
            if profile.name in self.profile_by_name:
                continue
            self.profiles.append(profile)
            self.profile_by_name[profile.name] = profile

    def profile_sort_key(self, profile):
        text = f"{profile.name} {profile.group_name} {profile.shape_type}".upper()
        family_order = [
            ("PURLIN", 0),
            ("ZED", 0),
            ("CEE", 0),
            ("UB", 0),
            ("UC", 1),
            ("PFC", 2),
            ("RHS", 3),
            ("SHS", 4),
            ("CHS", 5),
        ]
        family_rank = 99
        family_name = ""
        for token, rank in family_order:
            if re.search(rf"(^|[^A-Z]){token}([^A-Z]|$)", text):
                family_rank = rank
                family_name = token
                break
        numbers = [float(value) for value in re.findall(r"\d+(?:\.\d+)?", profile.name)]
        if family_name in {"SHS", "RHS", "CHS"}:
            # Tube names are typically depth * thickness; sort by depth first.
            numeric_key = numbers
        else:
            # UB/UC/PFC names are typically depth then mass; this still gives
            # smallest-to-largest behaviour within each family.
            numeric_key = numbers
        return (family_rank, numeric_key, profile.name)

    def all_profiles(self):
        return self.profiles

    def shs_profiles(self):
        shs = []
        for p in self.profiles:
            text = f"{p.name} {p.group_name} {p.shape_type}".upper()
            if "SHS" in text or "SQUARE HOLLOW" in text:
                shs.append(p)
        return shs

    def rhs_profiles(self):
        rhs = []
        for p in self.profiles:
            text = f"{p.name} {p.group_name} {p.shape_type}".upper()
            if "RHS" in text or "RECTANGULAR HOLLOW" in text:
                rhs.append(p)
        return rhs

    def chs_profiles(self):
        chs = []
        for profile in self.profiles:
            text = f"{profile.name} {profile.group_name} {profile.shape_type}".upper()
            if "CHS" in text or "CIRCULAR HOLLOW" in text:
                chs.append(profile)
        return chs

    def ub_profiles(self):
        ub = []
        for p in self.profiles:
            text = f"{p.name} {p.group_name} {p.shape_type}".upper()
            if re.search(r"(^|[^A-Z])UB([^A-Z]|$)", text):
                ub.append(p)
        return ub

    def purlin_profiles(self):
        purlins = []
        for p in self.profiles:
            text = f"{p.name} {p.group_name} {p.shape_type}".upper()
            if any(token in text for token in ["PURLIN", "ZED", "CEE", "Z PURLIN", "C PURLIN"]):
                purlins.append(p)
        return purlins

    def end_wall_mullion_profiles(self):
        profiles = []
        for profile in self.profiles:
            text = f"{profile.name} {profile.group_name} {profile.shape_type}".upper()
            if any(token in text for token in ["PURLIN", "ZED", "CEE"]):
                continue
            if re.search(r"(^|[^A-Z])(UB|UC|WB|PFC|RHS|SHS|CHS)([^A-Z]|$)", text) or any(
                token in text for token in ["HOLLOW", "TUBE", "CHANNEL"]
            ):
                profiles.append(profile)
        return profiles

    def get(self, name):
        if name in self.profile_by_name:
            return self.profile_by_name[name]
        clean_name = clean_profile_label(name)
        return self.profile_by_name.get(clean_name)
