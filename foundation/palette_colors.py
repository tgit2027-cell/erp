import math
import re


HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
WHITE = "#FFFFFF"
BLACK = "#000000"
INK = "#192820"


def normalize_hex_color(value):
    if not isinstance(value, str) or not HEX_COLOR_RE.fullmatch(value):
        raise ValueError("Enter a six-digit hex color such as #B24F00.")
    return value.upper()


def _channels(color):
    return tuple(int(color[index:index + 2], 16) for index in (1, 3, 5))


def _hex(channels):
    return "#" + "".join(f"{channel:02X}" for channel in channels)


def _mix(color, target, target_weight):
    return _hex(
        tuple(
            math.floor(source * (1 - target_weight) + destination * target_weight + 0.5)
            for source, destination in zip(_channels(color), _channels(target))
        )
    )


def _luminance(color):
    channels = [channel / 255 for channel in _channels(color)]
    linear = [
        channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4
        for channel in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast_ratio(first, second):
    lighter, darker = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def build_custom_palette_colors(accent_color):
    accent = normalize_hex_color(accent_color)
    colors = {
        "--brand": accent,
        "--green": accent,
        "--brand-deep": _mix(accent, BLACK, 0.20),
        "--brand-mid": _mix(accent, BLACK, 0.15),
        "--brand-soft": _mix(accent, WHITE, 0.90),
        "--sidebar": _mix(accent, BLACK, 0.74),
        "--sidebar-muted": _mix(accent, WHITE, 0.74),
        "--focus": accent,
        "--line": _mix(accent, WHITE, 0.82),
        "--sand": _mix(accent, WHITE, 0.97),
    }
    ratios = (
        (WHITE, colors["--brand"], 4.5, "white button text"),
        (colors["--brand-mid"], WHITE, 4.5, "accent text on white"),
        (colors["--brand-mid"], colors["--brand-soft"], 4.5, "accent text on the surface"),
        (INK, colors["--brand-soft"], 4.5, "dark text on the surface"),
        (colors["--sidebar-muted"], colors["--sidebar"], 4.5, "sidebar text"),
        (colors["--focus"], WHITE, 3.0, "focus indicator"),
    )
    for foreground, background, minimum, label in ratios:
        if contrast_ratio(foreground, background) < minimum:
            raise ValueError(
                f"This accent cannot meet the required {minimum:g}:1 contrast for {label}."
            )
    return colors
