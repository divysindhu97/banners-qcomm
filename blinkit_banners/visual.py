from __future__ import annotations

from dataclasses import dataclass, field

from PIL import Image

SAMPLE_EDGE = 160
PALETTE_SIZE = 5
FLAT_SPREAD_LIMIT = 4.5
GRADIENT_SPREAD_LIMIT = 16.0


@dataclass
class Visual:
    palette: list[str] = field(default_factory=list)
    dominant: str = ""
    background: str = "unknown"


def _hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def _palette(image: Image.Image) -> list[str]:
    small = image.resize((SAMPLE_EDGE, SAMPLE_EDGE), Image.Resampling.BILINEAR)
    quantised = small.quantize(colors=PALETTE_SIZE, method=Image.Quantize.MEDIANCUT)
    table = quantised.getpalette() or []
    counts = sorted(quantised.getcolors() or [], reverse=True)
    colours = []
    for _, index in counts[:PALETTE_SIZE]:
        colours.append(_hex(tuple(table[index * 3 : index * 3 + 3])))  # type: ignore[arg-type]
    return colours


def _background(image: Image.Image) -> str:
    """Describe the backdrop: flat colour, gradient, or photography.

    Sampling has to avoid the copy itself, whose glyph edges would make every
    creative look photographic. The left margin strip sits inboard of the rounded
    corners and outboard of the text, so it is almost always pure background.
    """
    width, height = image.size
    strip = image.convert("L").crop(
        (int(width * 0.01), int(height * 0.15), max(2, int(width * 0.05)), int(height * 0.85))
    )
    values = strip.tobytes()
    mean = sum(values) / len(values)
    spread = (sum((value - mean) ** 2 for value in values) / len(values)) ** 0.5

    if spread < FLAT_SPREAD_LIMIT:
        return "flat"
    if spread < GRADIENT_SPREAD_LIMIT:
        return "gradient"
    return "photo"


def describe(path: str) -> Visual:
    with Image.open(path) as handle:
        image = handle.convert("RGB")
        palette = _palette(image)
        return Visual(palette=palette, dominant=palette[0] if palette else "", background=_background(image))
