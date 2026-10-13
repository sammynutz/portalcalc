COLORBOND_CORE_COLOURS = {
    "Dover White": "#F9FBF1",
    "Surfmist": "#E4E2D5",
    "Evening Haze": "#C5C2AA",
    "Classic Cream": "#E9DCB8",
    "Paperbark": "#CABFA4",
    "Dune": "#B1ADA3",
    "Southerly": "#D2D1CB",
    "Shale Grey": "#BDBFBA",
    "Bluegum": "#969799",
    "Windspray": "#888B8A",
    "Gully": "#857E73",
    "Jasper": "#6C6153",
    "Wallaby": "#7F7C78",
    "Basalt": "#6D6C6E",
    "Woodland Grey": "#4B4C46",
    "Monument": "#323233",
    "Night Sky": "#000000",
    "Ironstone": "#3E434C",
    "Deep Ocean": "#364152",
    "Cottage Green": "#304C3C",
    "Pale Eucalypt": "#7C846A",
    "Manor Red": "#5E1D0E",
}

CLADDING_PROFILES = ["5-Rib (Trimdek)", "Corry (Corodek)"]


DEFAULT_FINISHES = {
    "roof": "Monument",
    "walls": "Monument",
    "barge": "Monument",
    "ridge_capping": "Monument",
    "corner_trim": "Monument",
    "gutter": "Monument",
    "roof_profile": "5-Rib (Trimdek)",
    "wall_profile": "5-Rib (Trimdek)",
    "skylights": "None",
    "skylight_run": "Full run",
    "show_slab": True,
}


def colour_hex(name, fallback="#D9DDDD"):
    return COLORBOND_CORE_COLOURS.get(str(name), fallback)
