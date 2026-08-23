from __future__ import annotations

import functools
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

import yaml

FUZZY_THRESHOLD = 0.84
MIN_FUZZY_LENGTH = 5


@dataclass
class Brand:
    name: str
    category: str
    aliases: tuple[str, ...]


@dataclass
class Match:
    value: str = ""
    confidence: float = 0.0
    source: str = ""

    def __bool__(self) -> bool:
        return bool(self.value)


def _normalise(text: str) -> str:
    text = text.lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _contains(alias: str, haystack: str) -> bool:
    if re.search(rf"\b{re.escape(alias)}\b", haystack):
        return True
    # OCR regularly drops the space between words ("Explorethe Vembleyrange"),
    # so fall back to a space-insensitive search for aliases long enough to be safe.
    squashed = alias.replace(" ", "")
    return len(squashed) >= 5 and squashed in haystack.replace(" ", "")


def _fuzzy(alias: str, tokens: list[str]) -> float:
    """Best similarity between the alias and any same-length window of tokens."""
    if len(alias) < MIN_FUZZY_LENGTH:
        return 0.0
    span = len(alias.split())
    best = 0.0
    for index in range(len(tokens) - span + 1):
        window = " ".join(tokens[index : index + span])
        best = max(best, SequenceMatcher(None, alias, window).ratio())
    return best


class Taxonomy:
    def __init__(self, path: str | Path = "taxonomy.yaml") -> None:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        self.brands = [
            Brand(name=name, category=body.get("category", ""), aliases=tuple(body.get("aliases", [])))
            for name, body in (raw.get("brands") or {}).items()
        ]
        self.categories: dict[str, tuple[str, ...]] = {
            name: tuple(words) for name, words in (raw.get("categories") or {}).items()
        }
        self.occasions: dict[str, tuple[str, ...]] = {
            name: tuple(words) for name, words in (raw.get("occasions") or {}).items()
        }

    def search(self, text: str) -> tuple[str, float]:
        """Best brand match in a blob of text, as (name, similarity)."""
        haystack = _normalise(text)
        if not haystack:
            return "", 0.0
        tokens = haystack.split()
        name, best = "", 0.0
        for brand in self.brands:
            for alias in brand.aliases:
                alias = _normalise(alias)
                if not alias:
                    continue
                if _contains(alias, haystack):
                    score = 1.0
                else:
                    score = _fuzzy(alias, tokens)
                    if score < FUZZY_THRESHOLD:
                        continue
                if score > best:
                    name, best = brand.name, score
        return name, best

    def match_brand(self, logo_text: str, copy_text: str, all_text: str) -> Match:
        """Resolve a brand from the strongest available evidence.

        A logo in the corner is an advertiser signal. A name written into the copy
        is nearly as good. A name merely legible on a product pack is weak: category
        banners show many brands' packs without being an ad for any of them.
        """
        tiers = (("logo", logo_text, 0.95), ("copy", copy_text, 0.85), ("product", all_text, 0.5))
        for source, text, ceiling in tiers:
            name, score = self.search(text)
            if name:
                return Match(name, round(score * ceiling, 2), source)
        return Match()

    def add_brand(self, name: str, category: str = "", aliases: tuple[str, ...] = ()) -> None:
        """Register a brand learned at runtime so later creatives match it."""
        self.brands.append(Brand(name=name, category=category, aliases=aliases or (name,)))
        self.__dict__.pop("protected", None)

    def is_generic(self, word: str) -> bool:
        """True when a word is one of our own category or occasion keywords."""
        candidate = _normalise(word)
        for mapping in (self.categories, self.occasions):
            for label, keywords in mapping.items():
                if candidate == _normalise(label) or candidate in {_normalise(k) for k in keywords}:
                    return True
        return False

    @functools.cached_property
    def protected(self) -> frozenset[str]:
        """Words that must never be re-split when repairing OCR spacing."""
        words: set[str] = set()
        sources: list[str] = []
        for brand in self.brands:
            sources.extend([brand.name, *brand.aliases])
        for mapping in (self.occasions, self.categories):
            for name, keywords in mapping.items():
                sources.extend([name, *keywords])
        for text in sources:
            words.update(word for word in _normalise(text).split() if len(word) >= 3)
        return frozenset(words)

    def category_for_brand(self, brand: str) -> str:
        for entry in self.brands:
            if entry.name == brand:
                return entry.category
        return ""

    def match_category(self, text: str) -> Match:
        haystack = _normalise(text)
        for name, words in self.categories.items():
            for word in words:
                if _contains(_normalise(word), haystack):
                    return Match(name, 0.7, "keyword")
        return Match()

    def match_occasion(self, text: str) -> Match:
        haystack = _normalise(text)
        for name, words in self.occasions.items():
            for word in words:
                if _contains(_normalise(word), haystack):
                    return Match(name, 0.8, "keyword")
        return Match()
