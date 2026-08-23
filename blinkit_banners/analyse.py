from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from . import layout as layout_module
from . import ocr, visual
from .brands import BrandRegistry
from .store import Store
from .taxonomy import Taxonomy
from .textclean import clean

def _looks_like_a_logo(headline: str, brand: str, taxonomy: Taxonomy) -> bool:
    """A lone line that is only the brand name is a wordmark, not a headline."""
    if not headline or not brand:
        return False
    candidate, _ = taxonomy.search(headline)
    return candidate == brand and len(headline.split()) <= 2


def analyse(path: str, registry: BrandRegistry) -> dict:
    taxonomy = registry.taxonomy
    with Image.open(path) as handle:
        width, height = handle.size

    blocks = ocr.read(path)
    result = layout_module.classify(blocks, width, height)

    protected = taxonomy.protected
    headline = clean(result.headline, protected)
    subheadline = clean(result.subheadline, protected)
    copy = " ".join(part for part in (headline, subheadline) if part)

    # The category read from the copy also seeds the category of any brand we are
    # about to meet for the first time.
    keyword_category = taxonomy.match_category(copy or result.all_text).value
    brand = registry.resolve(result, copy, keyword_category)

    archetype = result.archetype
    if _looks_like_a_logo(headline, brand.value, taxonomy) and not subheadline:
        headline, copy = "", ""
        archetype = "image_only"

    # A brand only glimpsed on a product pack does not define the creative's category.
    category = taxonomy.category_for_brand(brand.value) if brand.source != "product" else ""
    if not category:
        category = keyword_category

    appearance = visual.describe(path)

    return {
        "headline": headline,
        "subheadline": subheadline,
        "cta": result.cta,
        "has_ad_badge": int(result.has_ad_badge),
        "archetype": archetype,
        "text_side": result.text_side,
        "brand": brand.value,
        "brand_source": brand.source,
        "brand_confidence": brand.confidence,
        "category": category,
        "occasion": taxonomy.match_occasion(copy).value,
        "dominant_colour": appearance.dominant,
        "palette": json.dumps(appearance.palette),
        "background": appearance.background,
        "ocr_text": clean(result.all_text, protected),
    }


def analyse_pending(
    store: Store,
    taxonomy_path: str | Path = "taxonomy.yaml",
    force: bool = False,
    progress=None,
) -> dict:
    """OCR every creative that does not yet have attributes.

    Copy is read off the pixels and stored in SQLite. Nothing is hardcoded: a
    later scrape of a new creative goes through the same path automatically.
    """
    if force:
        store.forget_brands()
    registry = BrandRegistry(Taxonomy(taxonomy_path), store)
    done = set() if force else store.analysed_hashes()
    pending = [row for row in store.creatives() if row["phash"] not in done]
    iterable = progress(pending) if progress else pending

    resolved = 0
    analysed = 0
    for row in iterable:
        path = Path(row["file_path"])
        if not path.exists():
            continue
        values = analyse(str(path), registry)
        store.save_attributes(row["phash"], values)
        analysed += 1
        resolved += bool(values["brand"])

    return {
        "pending": len(pending),
        "analysed": analysed,
        "resolved": resolved,
        "learned": sorted(set(registry.learned_this_run)),
    }
