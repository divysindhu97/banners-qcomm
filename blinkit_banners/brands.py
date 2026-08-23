from __future__ import annotations

import re

from .layout import Layout
from .store import Store
from .taxonomy import Match, Taxonomy, _normalise

# Copy that names its advertiser outright. These read cleanly, so they are tried
# before the logo, whose OCR is often mangled ("Oadbury", "SUPERYOV").
NAME = r"([A-Za-z][\w'&.\-]{2,20})"
COPY_PATTERNS = (
    re.compile(rf"explore the {NAME}\s+range", re.I),
    re.compile(rf"powered by:?\s+{NAME}", re.I),
    re.compile(rf"switch to {NAME}", re.I),
    re.compile(rf"introducing (?:the )?{NAME}", re.I),
    re.compile(rf"only (?:on|at) {NAME}", re.I),
)

# Words that show up in the logo zone or a matched pattern but name no advertiser.
NOISE = frozenset(
    """
    ad ads new now off free flat upto up to get buy shop order more range sale offer
    deal deals combo pack packs value save extra best top only all your our the and
    with for from this that here start starts explore discover introducing powered
    """.split()
)

MIN_WORDMARK_CONFIDENCE = 0.6
MAX_WORDMARK_WORDS = 3


def _plausible(candidate: str, taxonomy: Taxonomy) -> bool:
    cleaned = _normalise(candidate)
    if not cleaned or len(cleaned) < 3 or len(cleaned) > 28:
        return False
    words = cleaned.split()
    if len(words) > MAX_WORDMARK_WORDS:
        return False
    if any(word in NOISE for word in words):
        return False
    if cleaned.isdigit() or not any(character.isalpha() for character in cleaned):
        return False
    # "Chocolate" or "Monsoon" describe the creative, they do not name a brand.
    return not taxonomy.is_generic(cleaned)


def _tidy(candidate: str) -> str:
    return re.sub(r"\s+", " ", candidate.strip(" :,.-\u2013")).strip()


class BrandRegistry:
    """Brand knowledge that grows as creatives arrive.

    `taxonomy.yaml` seeds the registry with aliases and categories. Everything the
    analyser reads off a creative and cannot already name is recorded, and is then
    matchable on every later run, so the vocabulary tracks the archive instead of
    needing to be maintained by hand.
    """

    def __init__(self, taxonomy: Taxonomy, store: Store | None = None):
        self.taxonomy = taxonomy
        self.store = store
        self.learned_this_run: list[str] = []
        if store is not None:
            for row in store.known_brands():
                taxonomy.add_brand(row["name"], row["category"] or "", (row["name"], row["slug"]))

    def _candidates(self, layout: Layout, copy: str) -> list[tuple[str, str]]:
        found: list[tuple[str, str]] = []
        for pattern in COPY_PATTERNS:
            match = pattern.search(copy)
            if match:
                found.append((_tidy(match.group(1)), "copy"))
        wordmark = layout.wordmark
        if wordmark and wordmark.confidence >= MIN_WORDMARK_CONFIDENCE:
            found.append((_tidy(wordmark.text), "logo"))
        return found

    def resolve(self, layout: Layout, copy: str, category_hint: str = "") -> Match:
        """Name the advertiser, learning the name if this is the first sighting."""
        known = self.taxonomy.match_brand(layout.logo_blob, copy, layout.all_text)
        if known:
            return known

        for candidate, origin in self._candidates(layout, copy):
            if not _plausible(candidate, self.taxonomy):
                continue
            slug = _normalise(candidate).replace(" ", "")
            self.taxonomy.add_brand(candidate, category_hint, (candidate, slug))
            if self.store is not None:
                self.store.remember_brand(slug, candidate, category_hint, origin)
            self.learned_this_run.append(candidate)
            # A freshly learned name is as trustworthy as where it was read from.
            return Match(candidate, 0.95 if origin == "logo" else 0.85, f"learned-{origin}")
        return Match()
