from __future__ import annotations

import re
from dataclasses import dataclass, field

from .ocr import TextBlock

CTA_PHRASES = (
    "shop now",
    "order now",
    "buy now",
    "explore now",
    "shop all",
    "order",
    "explore",
    "know more",
    "see more",
    "get started",
    "grab now",
    "book now",
    "apply now",
    "try now",
)

# Copy sits in a left-aligned column; product shots and packaging text sit beside it.
# Blinkit creatives are uniformly copy-left, so the search is deliberately left-biased.
COPY_COLUMN_TOLERANCE = 0.035  # of image width
COPY_ZONE_RIGHT = 0.45
MAX_COPY_SKEW = 6.0  # degrees
LOGO_ZONE_LEFT = 0.55
LOGO_ZONE_TOP = 0.35
BADGE_ZONE = 0.75


@dataclass
class Layout:
    headline: str = ""
    subheadline: str = ""
    cta: str = ""
    has_ad_badge: bool = False
    archetype: str = "image_only"
    text_side: str = "none"
    logo_blocks: list[TextBlock] = field(default_factory=list)
    all_text: str = ""

    @property
    def copy_text(self) -> str:
        return " ".join(part for part in (self.headline, self.subheadline) if part)

    @property
    def logo_text(self) -> list[str]:
        return [block.text for block in self.logo_blocks]

    @property
    def logo_blob(self) -> str:
        return " ".join(self.logo_text)

    @property
    def wordmark(self) -> TextBlock | None:
        """Largest line in the logo zone: the advertiser's name, when there is one."""
        return max(self.logo_blocks, key=lambda block: block.height, default=None)


def _normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()


def _is_ad_badge(block: TextBlock, width: int, height: int) -> bool:
    if _normalise(block.text) not in {"ad", "ads"}:
        return False
    return block.left > width * BADGE_ZONE and block.top > height * BADGE_ZONE


def _is_cta(block: TextBlock, height: int) -> bool:
    return _normalise(block.text) in CTA_PHRASES and block.centre_y > height * 0.55


def _copy_margin(blocks: list[TextBlock], width: int) -> int | None:
    """Find the shared left edge of the copy column.

    Copy is left-aligned and is the largest type in the creative, so the column is
    the one holding the tallest line. Ranking by the tallest rather than the total
    stops a stack of small packaging labels from outvoting a single big headline.
    """

    def bucket_of(block: TextBlock) -> int:
        return round(block.left / max(1, width * 0.02))

    # Angled text belongs to artwork (a tilted gift card, a label on a pack). Its
    # bounding box is inflated by the rotation, which would otherwise let it pose
    # as the largest type on the creative.
    candidates = [
        block
        for block in blocks
        if block.left < width * COPY_ZONE_RIGHT and abs(block.skew) <= MAX_COPY_SKEW
    ]
    if not candidates:
        return None
    buckets: dict[int, float] = {}
    for block in candidates:
        key = bucket_of(block)
        buckets[key] = max(buckets.get(key, 0.0), float(block.height))
    best = max(buckets, key=lambda key: buckets[key])
    return min(block.left for block in candidates if bucket_of(block) == best)


def _group_lines(blocks: list[TextBlock]) -> list[list[TextBlock]]:
    """Split stacked lines into paragraphs on vertical gap or font-size change."""
    groups: list[list[TextBlock]] = []
    for block in sorted(blocks, key=lambda b: b.top):
        if not groups:
            groups.append([block])
            continue
        previous = groups[-1][-1]
        gap = block.top - previous.bottom
        ratio = block.height / max(1, previous.height)
        if gap > previous.height * 0.75 or ratio < 0.7 or ratio > 1.4:
            groups.append([block])
        else:
            groups[-1].append(block)
    return groups


def _join(group: list[TextBlock]) -> str:
    return " ".join(block.text for block in sorted(group, key=lambda b: b.top))


def _mean_height(group: list[TextBlock]) -> float:
    return sum(block.height for block in group) / len(group)


def classify(blocks: list[TextBlock], width: int, height: int) -> Layout:
    layout = Layout(all_text=" ".join(block.text for block in blocks))
    if not blocks:
        return layout

    layout.logo_blocks = [
        block
        for block in blocks
        if block.left > width * LOGO_ZONE_LEFT and block.centre_y < height * LOGO_ZONE_TOP
    ]

    body: list[TextBlock] = []
    for block in blocks:
        if _is_ad_badge(block, width, height):
            layout.has_ad_badge = True
        elif _is_cta(block, height):
            # Keep the lowest-risk match: CTAs sit alone near the bottom of the copy.
            if not layout.cta:
                layout.cta = block.text
        else:
            body.append(block)

    margin = _copy_margin(body, width)
    if margin is None:
        return layout

    tolerance = max(12.0, width * COPY_COLUMN_TOLERANCE)
    column = [
        block
        for block in body
        if abs(block.left - margin) <= tolerance and abs(block.skew) <= MAX_COPY_SKEW
    ]
    if not column:
        return layout

    groups = _group_lines(column)
    groups.sort(key=lambda group: (-_mean_height(group), group[0].top))
    headline_group = groups[0]
    layout.headline = _join(headline_group)

    headline_bottom = max(block.bottom for block in headline_group)
    headline_height = _mean_height(headline_group)
    followers = [
        group
        for group in groups[1:]
        if group[0].top >= headline_bottom and _mean_height(group) < headline_height
    ]
    if followers:
        followers.sort(key=lambda group: group[0].top)
        layout.subheadline = _join(followers[0])

    centre = (margin + max(block.right for block in column)) / 2
    if centre < width * 0.45:
        layout.text_side = "left"
    elif centre > width * 0.55:
        layout.text_side = "right"
    else:
        layout.text_side = "center"

    parts = ["headline"]
    if layout.subheadline:
        parts.append("sub")
    if layout.cta:
        parts.append("cta")
    layout.archetype = "_".join(parts)
    return layout
