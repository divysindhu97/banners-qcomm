from __future__ import annotations

from dataclasses import dataclass

from .config import DetectionConfig
from .hierarchy import Node, Rect

IMAGE_CLASS_HINTS = ("ImageView", "ImageButton", "AppCompatImageView", "ComposeView")
CAROUSEL_CLASS_HINTS = ("ViewPager", "RecyclerView", "HorizontalScrollView", "Pager")
# Layout classes never hold pixels themselves. Blinkit places empty ones over the
# feed as decoration (`bottom_clip_lottie_image_view`, `strip_container`); cropping
# them yields whatever happens to render underneath.
LAYOUT_CLASS_HINTS = (
    "ViewGroup", "FrameLayout", "LinearLayout", "RelativeLayout", "ConstraintLayout",
    "GridView", "RecyclerView", "ScrollView", "ViewPager", "CardView", "TableLayout",
)


@dataclass
class BannerCandidate:
    rect: Rect
    node: Node
    carousel: Node | None = None
    # True when matched by resource id rather than by shape.
    from_id: bool = False

    @property
    def slot_key(self) -> str:
        rid = self.node.resource_id.rsplit("/", 1)[-1]
        return rid or self.node.short_class


def _screen_rect(root: Node) -> Rect:
    rect = root.bounds
    if rect.area:
        return rect
    # Compose-heavy trees sometimes leave the root unbounded; fall back to children.
    right = max((n.bounds.right for n in root.walk()), default=0)
    bottom = max((n.bounds.bottom for n in root.walk()), default=0)
    return Rect(0, 0, right, bottom)


def _matches_any(value: str, patterns: list[str]) -> bool:
    return any(p in value for p in patterns if p)


def _id_segment(resource_id: str) -> str:
    return resource_id.rsplit("/", 1)[-1]


def _id_in(node: Node, patterns: list[str]) -> bool:
    """Exact match on the id segment.

    Substring matching is unsafe on this app: `image_view` is the hero banner but
    `image_view_primary` is a product thumbnail.
    """
    return bool(patterns) and _id_segment(node.resource_id) in set(patterns)


def _desc_in(node: Node, patterns: list[str]) -> bool:
    desc = node.content_desc.lower()
    return bool(desc) and any(p.lower() in desc for p in patterns if p)


def _in_denied_container(node: Node, denied: list[str]) -> bool:
    """True if the node or any ancestor is a container we never want to read."""
    if not denied:
        return False
    blocked = set(denied)
    if _id_segment(node.resource_id) in blocked:
        return True
    return any(_id_segment(a.resource_id) in blocked for a in node.ancestors())


def _is_image_like(node: Node) -> bool:
    if _matches_any(node.cls, list(IMAGE_CLASS_HINTS)):
        return True
    if _matches_any(node.cls, list(LAYOUT_CLASS_HINTS)):
        return False
    # Compose and custom views often render as childless generic nodes.
    return not node.children


def _in_carousel(node: Node) -> Node | None:
    for ancestor in node.ancestors():
        if ancestor.scrollable or _matches_any(ancestor.cls, list(CAROUSEL_CLASS_HINTS)):
            if ancestor.bounds.width and ancestor.bounds.aspect <= 1.2:
                return ancestor
    return None


def find_banners(root: Node, cfg: DetectionConfig) -> list[BannerCandidate]:
    """Locate banner views, preferring known resource ids and falling back to shape.

    Both passes run by default and their results are merged. Resource ids are the
    accurate signal, but they change between app releases, so the geometry pass
    keeps the scraper producing something when they do. Set `strict_ids` to trust
    the allowlist alone.
    """
    screen = _screen_rect(root)
    if not screen.area:
        return []

    named = _match_by_selector(root, cfg, screen)
    if cfg.strict_ids and (cfg.resource_id_allow or cfg.content_desc_allow):
        return _dedupe_overlapping(named)
    return _dedupe_overlapping(named + _match_by_shape(root, cfg, screen))


def _excluded(node: Node, cfg: DetectionConfig) -> bool:
    return _id_in(node, cfg.resource_id_deny) or _in_denied_container(node, cfg.container_deny)


def _visible_band(cfg: DetectionConfig, screen: Rect) -> tuple[float, float]:
    return cfg.exclude_top_ratio * screen.height, (1.0 - cfg.exclude_bottom_ratio) * screen.height


def _is_visible(rect: Rect, cfg: DetectionConfig, band: tuple[float, float]) -> bool:
    top_limit, bottom_limit = band
    if cfg.require_fully_visible:
        return rect.top >= top_limit and rect.bottom <= bottom_limit
    centre_y = rect.top + rect.height / 2
    return top_limit <= centre_y <= bottom_limit


def _match_by_selector(root: Node, cfg: DetectionConfig, screen: Rect) -> list[BannerCandidate]:
    if not (cfg.resource_id_allow or cfg.content_desc_allow):
        return []
    # Deliberately looser than the shape pass: if you have named the view, trust it
    # even when a redesign changes its proportions.
    min_width = 0.35 * screen.width
    band = _visible_band(cfg, screen)
    found = []
    for node in root.walk():
        if node.bounds.width < min_width or node.bounds.height < cfg.min_height_px:
            continue
        if not _is_visible(node.bounds, cfg, band):
            continue
        if _excluded(node, cfg):
            continue
        if not (_id_in(node, cfg.resource_id_allow) or _desc_in(node, cfg.content_desc_allow)):
            continue
        found.append(BannerCandidate(rect=node.bounds, node=node, carousel=_in_carousel(node), from_id=True))
    return found


def _match_by_shape(root: Node, cfg: DetectionConfig, screen: Rect) -> list[BannerCandidate]:
    min_width = cfg.min_width_ratio * screen.width
    band = _visible_band(cfg, screen)

    found = []
    for node in root.walk():
        rect = node.bounds
        if rect.width < min_width or rect.height < cfg.min_height_px:
            continue
        if not (cfg.min_aspect <= rect.aspect <= cfg.max_aspect):
            continue
        if not _is_visible(rect, cfg, band):
            continue
        if _excluded(node, cfg):
            continue
        if not _is_image_like(node):
            continue

        found.append(BannerCandidate(rect=rect, node=node, carousel=_in_carousel(node)))
    return found


def _dedupe_overlapping(candidates: list[BannerCandidate], iou_threshold: float = 0.85) -> list[BannerCandidate]:
    """Collapse views covering the same pixels, letting id matches win over shape ones."""
    kept: list[BannerCandidate] = []
    for candidate in sorted(candidates, key=lambda c: (not c.from_id, -c.rect.area)):
        if any(candidate.rect.iou(k.rect) >= iou_threshold for k in kept):
            continue
        kept.append(candidate)
    return sorted(kept, key=lambda c: (c.rect.top, c.rect.left))


def find_carousels(root: Node, cfg: DetectionConfig) -> list[Node]:
    screen = _screen_rect(root)
    if not screen.area:
        return []
    min_width = cfg.min_width_ratio * screen.width

    found: list[Node] = []
    for node in root.walk():
        rect = node.bounds
        if rect.width < min_width or rect.height < cfg.min_height_px:
            continue
        if not (cfg.min_aspect <= rect.aspect <= cfg.max_aspect):
            continue
        if not (node.scrollable or _matches_any(node.cls, list(CAROUSEL_CLASS_HINTS))):
            continue
        if _excluded(node, cfg):
            continue
        if any(rect.iou(f.bounds) >= 0.85 for f in found):
            continue
        found.append(node)
    return found
