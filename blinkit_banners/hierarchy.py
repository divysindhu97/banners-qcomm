from __future__ import annotations

import re
from dataclasses import dataclass, field
from xml.etree import ElementTree

BOUNDS_RE = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")


@dataclass
class Rect:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    @property
    def area(self) -> int:
        return max(0, self.width) * max(0, self.height)

    @property
    def aspect(self) -> float:
        return self.height / self.width if self.width else 0.0

    def scaled(self, factor: float) -> "Rect":
        return Rect(
            int(self.left * factor),
            int(self.top * factor),
            int(self.right * factor),
            int(self.bottom * factor),
        )

    def intersection_area(self, other: "Rect") -> int:
        dx = min(self.right, other.right) - max(self.left, other.left)
        dy = min(self.bottom, other.bottom) - max(self.top, other.top)
        return dx * dy if dx > 0 and dy > 0 else 0

    def iou(self, other: "Rect") -> float:
        inter = self.intersection_area(other)
        union = self.area + other.area - inter
        return inter / union if union else 0.0

    def as_tuple(self) -> tuple[int, int, int, int]:
        return (self.left, self.top, self.right, self.bottom)


@dataclass
class Node:
    cls: str
    resource_id: str
    content_desc: str
    text: str
    hint: str
    bounds: Rect
    clickable: bool
    scrollable: bool
    depth: int
    children: list["Node"] = field(default_factory=list)
    parent: "Node | None" = field(default=None, repr=False)

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()

    def ancestors(self):
        node = self.parent
        while node is not None:
            yield node
            node = node.parent

    @property
    def short_class(self) -> str:
        return self.cls.rsplit(".", 1)[-1]

    def describe(self) -> str:
        parts = [self.short_class]
        if self.resource_id:
            parts.append(f"id={self.resource_id.rsplit('/', 1)[-1]}")
        if self.content_desc:
            parts.append(f"desc={self.content_desc[:40]!r}")
        if self.text:
            parts.append(f"text={self.text[:40]!r}")
        parts.append(f"{self.bounds.width}x{self.bounds.height}")
        return " ".join(parts)


def _parse_bounds(value: str) -> Rect:
    match = BOUNDS_RE.match(value or "")
    if not match:
        return Rect(0, 0, 0, 0)
    left, top, right, bottom = (int(g) for g in match.groups())
    return Rect(left, top, right, bottom)


def parse(xml: str) -> Node:
    """Turn a uiautomator hierarchy dump into a Node tree."""
    root_element = ElementTree.fromstring(xml)

    def build(element, depth: int, parent: Node | None) -> Node:
        node = Node(
            cls=element.get("class", ""),
            resource_id=element.get("resource-id", ""),
            content_desc=element.get("content-desc", ""),
            text=element.get("text", ""),
            hint=element.get("hint", ""),
            bounds=_parse_bounds(element.get("bounds", "")),
            clickable=element.get("clickable") == "true",
            scrollable=element.get("scrollable") == "true",
            depth=depth,
            parent=parent,
        )
        for child in element:
            node.children.append(build(child, depth + 1, node))
        return node

    # The <hierarchy> root has no bounds; use its first real child when present.
    children = list(root_element)
    if root_element.tag == "hierarchy" and len(children) == 1:
        return build(children[0], 0, None)
    return build(root_element, 0, None)
