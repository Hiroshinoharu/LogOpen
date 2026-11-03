"""Generate saved light/dark and text-size variants from the bundled stylesheet."""

import re


LIGHT = {
    "#0d141f": "#f8fafc", "#141f2d": "#f0f4f8", "#121e2c": "#ffffff",
    "#162231": "#ffffff", "#192636": "#e7edf4", "#182636": "#edf2f7",
    "#1e3043": "#e4edf5", "#203244": "#e4edf5", "#223246": "#e4edf5",
    "#26384e": "#dde7f0", "#233044": "#c8d5e2", "#29394c": "#c2cedb",
    "#263346": "#cbd5e1", "#304258": "#bccbd9", "#2b3d51": "#bfccda",
    "#0b3029": "#07352b", "#e5edf6": "#172b40", "#edf3fa": "#10243a",
    "#dce8f6": "#22394e", "#c4d1e1": "#334b63", "#c5d3e4": "#334b63",
    "#d1deef": "#253d55", "#a5b3c6": "#4d6278", "#b2c1d4": "#425b72",
    "#b8c9df": "#425b72", "#8998aa": "#61758a", "#94a4ba": "#61758a",
    "#80e5d3": "#086a59", "#afcaff": "#245da4", "#adcaff": "#245da4",
    "#f4bc72": "#82500a", "#ffd18a": "#78500a", "#ffadb7": "#a92740",
    "#ffbd95": "#95410d", "#15352f": "#e3f4ed", "#14312f": "#e3f4ed",
    "#142d30": "#e8f5ef", "#142333": "#eaf1fa", "#192e3b": "#e4f1f7",
    "#382f20": "#fff1cf", "#3a222d": "#ffe8ed", "#1b2b46": "#e4edfc",
    "#285348": "#6d9c8e", "#645233": "#bea46d", "#65404e": "#bc8493",
    "#2a4259": "#d2e4ef", "#22374c": "#d2e4ef", "#172737": "#c9deea",
    "#111b29": "#ffffff", "#0f1926": "#f0f4f8", "#1e2c3e": "#e3ebf3",
    "#202f43": "#edf2f7", "#172e32": "#e3f4ed", "#1a3a3c": "#d4ebe3",
    "#becbdd": "#405970", "#cad7e8": "#334b63", "#d3dfed": "#334b63",
    "#dce6f2": "#334b63", "#9baac0": "#61758a", "#382c20": "#fff1cf",
}


def themed_stylesheet(base, theme="dark", size="normal"):
    """Keep semantic priorities while changing readable surfaces and scaling declared fonts."""
    if theme == "light":
        base = re.sub(r"#[0-9a-fA-F]{6}", lambda match: LIGHT.get(match.group().lower(), match.group()), base)
        base += '\nQLabel#metricValue[tone="teal"] { color: #086a59; }\n'
    scale = {"normal": 1, "large": 1.15, "larger": 1.3}.get(size, 1)
    return re.sub(r"font-size:\s*(\d+)px", lambda match: f"font-size: {round(int(match.group(1)) * scale)}px", base)
