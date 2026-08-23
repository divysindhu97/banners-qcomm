"""Tests for copy extraction and attribute inference.

Coordinates mirror measurements taken from real Blinkit creatives so the cases
stay honest about what the classifier actually has to cope with.
"""

from __future__ import annotations

import pytest

from blinkit_banners.layout import classify
from blinkit_banners.ocr import TextBlock
from blinkit_banners.taxonomy import Taxonomy
from blinkit_banners.textclean import clean

HERO = (1024, 716)
TILE = (586, 415)


def block(text, left, top, height, *, width=300, confidence=0.85, skew=0.0):
    return TextBlock(
        text=text,
        left=left,
        top=top,
        right=left + width,
        bottom=top + height,
        confidence=confidence,
        skew=skew,
    )


@pytest.fixture(scope="module")
def taxonomy():
    return Taxonomy()


def test_headline_and_subheadline_split_on_font_size():
    """Two headline lines stay together; the smaller line below becomes the sub."""
    blocks = [
        block("Keep the party", 60, 77, 72),
        block("going with Lay's", 60, 167, 72),
        block("With no artificial colours", 61, 286, 37),
        block("Shop now", 94, 589, 45),
        block("Ad", 943, 651, 26, width=40),
    ]
    result = classify(blocks, *HERO)
    assert result.headline == "Keep the party going with Lay's"
    assert result.subheadline == "With no artificial colours"
    assert result.cta == "Shop now"
    assert result.has_ad_badge
    assert result.archetype == "headline_sub_cta"
    assert result.text_side == "left"


def test_a_stack_of_packaging_labels_does_not_outvote_the_headline():
    """The real failure on the L'Oreal tile: four tiny pack lines beat one big headline."""
    blocks = [
        block("Frizz Free Days", 37, 47, 44),
        block("WishCare", 225, 264, 16, width=60),
        block("Multi-Peptide", 226, 282, 13, width=60),
        block("Anti Hairfall", 227, 290, 11, width=60),
        block("Shampoo", 226, 297, 12, width=60),
    ]
    result = classify(blocks, *TILE)
    assert result.headline == "Frizz Free Days"
    assert result.subheadline == ""
    assert result.archetype == "headline"


def test_rotated_artwork_text_is_ignored():
    """Text on a tilted gift card has an inflated box and must not pose as copy."""
    blocks = [
        block("Make their Rakhi", 62, 80, 55),
        block("gift truly personal", 62, 170, 55),
        block("Get e-gift cards", 62, 285, 38),
        block("Superdry", 460, 390, 95, width=210, skew=-17.0),
    ]
    result = classify(blocks, *HERO)
    assert result.headline == "Make their Rakhi gift truly personal"
    assert "Superdry" not in result.headline


def test_logo_zone_text_is_collected_separately():
    blocks = [
        block("Enjoy the monsoon", 63, 80, 64),
        block("Saffola", 858, 87, 26, width=90),
    ]
    result = classify(blocks, *HERO)
    assert result.logo_text == ["Saffola"]
    assert result.headline == "Enjoy the monsoon"


def test_ad_badge_only_counts_in_the_corner():
    corner = classify([block("Ad", 943, 651, 26, width=40)], *HERO)
    assert corner.has_ad_badge
    middle = classify([block("Ad", 400, 300, 26, width=40)], *HERO)
    assert not middle.has_ad_badge


def test_empty_input_is_image_only():
    result = classify([], *HERO)
    assert result.archetype == "image_only"
    assert result.headline == ""


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Keepthe party going", "Keep the party going"),
        ("Explorethe SuperYou range", "Explore the SuperYou range"),
        ("Giftsetsand toys", "Gift sets and toys"),
        ("Getsupplements,massagers", "Get supplements, massagers"),
        ("Get up to 60%OFF", "Get up to 60% OFF"),
        ("Indiansweets, chocolates&more", "Indian sweets, chocolates & more"),
        ("ThisRakshaBandhan,get bags", "This Raksha Bandhan, get bags"),
        ("Poweredby:L'OREAL", "Powered by: L'OREAL"),
    ],
)
def test_glued_words_are_separated(raw, expected, taxonomy):
    assert clean(raw, taxonomy.protected) == expected


@pytest.mark.parametrize("name", ["Saffola", "Vembley", "Amayu", "Portronics", "Whiskas", "Titan"])
def test_brand_names_survive_cleaning(name, taxonomy):
    assert clean(f"Enjoy {name} today", taxonomy.protected) == f"Enjoy {name} today"


@pytest.mark.parametrize(
    ("misread", "expected"),
    [("Oadbury", "Cadbury"), ("SUPERYOV", "SuperYou"), ("LOREAL", "L'Oreal")],
)
def test_ocr_misreads_still_resolve_the_brand(misread, expected, taxonomy):
    assert taxonomy.match_brand(misread, "", "").value == expected


def test_missing_space_does_not_hide_a_brand(taxonomy):
    """"Explorethe Vembleyrange" must still resolve to Vembley."""
    match = taxonomy.match_brand("", "Explorethe Vembleyrange", "")
    assert match.value == "Vembley"


def test_a_logo_outranks_a_name_on_a_product_pack(taxonomy):
    match = taxonomy.match_brand("Whiskas", "Midnight Essentials", "Durex pack")
    assert (match.value, match.source) == ("Whiskas", "logo")


def test_a_pack_only_sighting_is_reported_as_weak(taxonomy):
    """Category banners show many brands' packs without advertising any of them."""
    match = taxonomy.match_brand("", "Midnight Essentials", "Durex")
    assert match.source == "product"
    assert match.confidence <= 0.5


def test_occasion_and_category_come_from_the_copy(taxonomy):
    assert taxonomy.match_occasion("Enjoy the monsoon with a classic taste").value == "Monsoon"
    assert taxonomy.match_occasion("The perfect Rakhi gifts start here").value == "Raksha Bandhan"
    assert taxonomy.match_category("Anti-frizz care for smooth hair").value == "Hair Care"
