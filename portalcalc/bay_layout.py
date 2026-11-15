"""Building bays entered as a list: ``7*8000, 7000`` or ``3*8000 2*7500 8000``.

The bay list is the building's length input. Each entry is a bay length in mm,
optionally repeated as ``count*length``; entries are separated by commas,
semicolons or spaces (the Tekla grid style). Bays run from the front gridline
to the back.

Everything else follows from the list:
- the building length is the sum of the bays;
- gridlines (portal frames) sit at the cumulative stations;
- the **design bay** is the longest bay. A single 2D frame design uses it as
  its tributary width and as the purlin/girt span, which covers every frame
  and every bay. With building loads each gridline uses its own tributary.

Presets before the bay list stored a building length and one bay size. They
convert with the old rule: full bays from the front, then a shorter last bay
for any remainder (``bays_from_length``), so their gridlines are unchanged.
"""
from __future__ import annotations

import math
import re

MIN_BAY_MM = 500.0
MAX_BAYS = 500
_TOKEN = re.compile(r"^(?:(\d+)\s*[*xX]\s*)?(\d+(?:\.\d*)?|\.\d+)$")
EXAMPLE = "7*8000, 7000"


class BayListError(ValueError):
    """The bay list text cannot be read."""


def parse_bays(text) -> list:
    """Bay lengths in mm from text such as ``"3*8000, 2*7500, 8000"``."""
    raw = str(text or "").strip()
    if not raw:
        raise BayListError(f"Enter the bays in mm, e.g. {EXAMPLE}")
    # Allow spaces around '*' ("3 * 8000") before splitting on whitespace.
    raw = re.sub(r"\s*([*xX])\s*", r"\1", raw)
    bays = []
    for token in (t for t in re.split(r"[,;\s]+", raw) if t):
        match = _TOKEN.match(token)
        if not match:
            raise BayListError(f"Can't read '{token}'. Enter bays in mm, e.g. {EXAMPLE}")
        count = int(match.group(1)) if match.group(1) else 1
        length = float(match.group(2))
        if count < 1:
            raise BayListError(f"'{token}': the bay count must be at least 1")
        if not math.isfinite(length) or length < MIN_BAY_MM:
            raise BayListError(f"'{token}': bays are in mm and must be at least {MIN_BAY_MM:g} mm "
                               f"(e.g. {EXAMPLE})")
        bays.extend([length] * count)
        if len(bays) > MAX_BAYS:
            raise BayListError(f"At most {MAX_BAYS} bays")
    return bays


def _number(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".")


def format_bays(bays) -> str:
    """Compact text for a bay list: consecutive equal bays as ``count*length``."""
    groups = []
    for bay in bays:
        if groups and abs(groups[-1][1] - bay) <= 1e-6:
            groups[-1][0] += 1
        else:
            groups.append([1, float(bay)])
    return ", ".join(_number(length) if count == 1 else f"{count}*{_number(length)}"
                     for count, length in groups)


def bays_from_length(length_mm: float, bay_mm: float) -> list:
    """The pre-bay-list rule: full bays from the front and a shorter last bay.

    A last bay within 1e-6 mm of the full bay counts as full, so a length that
    is a whole number of bays to floating-point accuracy gives equal bays.
    """
    length, bay = float(length_mm), float(bay_mm)
    if not (math.isfinite(length) and math.isfinite(bay)) or length <= 0 or bay <= 0:
        raise BayListError("Building length and bay size must be greater than zero")
    count = math.ceil(length / bay - 1e-9)
    if count > MAX_BAYS:
        raise BayListError(f"At most {MAX_BAYS} bays")
    bays = [bay] * count
    last = length - (count - 1) * bay
    if abs(last - bay) > 1e-6:
        bays[-1] = last
    return bays


def stations(bays) -> list:
    """Gridline positions from the front, mm."""
    out = [0.0]
    for bay in bays:
        out.append(out[-1] + float(bay))
    return out


def length_mm(bays) -> float:
    return float(sum(bays))


def design_bay_mm(bays) -> float:
    """The longest bay: the single-frame tributary and secondary-member span."""
    return float(max(bays))


def summary(bays) -> str:
    """``63 m long · 8 bays · design bay 8 m`` (unequal bays say so)."""
    unequal = max(bays) - min(bays) > 1e-6
    text = (f"{_number(length_mm(bays) / 1000.0)} m long · {len(bays)} bay{'s' if len(bays) != 1 else ''}"
            f" · design bay {_number(design_bay_mm(bays) / 1000.0)} m")
    return text + (" (longest)" if unequal else "")
