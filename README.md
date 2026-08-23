# Blinkit banner archive

Captures banner creatives from the Blinkit Android app (`com.grofers.customerapp`) by
driving the real app over ADB, screenshotting the home feed, and cropping out banner
views. Deduplicates with a perceptual hash so one banner seen twenty times is stored once
with twenty sightings.

Verified working against an Android Studio emulator (Pixel 9 Pro, 1280x2856). A clean
sweep of the home feed yields around 17 distinct creatives in roughly three minutes.

## Why on-device screenshots

Blinkit runs banners in the app but not on the website, so there is no page to scrape.
Driving the real app is also the least conspicuous option — a logged-in device browsing at
human pace looks like a user; a headless loop replaying the layout API does not.

The trade-off is screen-resolution crops with no campaign metadata. If you later want
original CDN URLs, brand IDs, and deeplink targets, that means intercepting the layout API
with mitmproxy on a rooted device — a different and much larger project.

## What Blinkit can and cannot see

**Invisible to them.** Reading the view hierarchy — including looking up the same resource
IDs every run — happens locally through Android's accessibility layer and produces no
network traffic. They cannot see which identifiers you queried, that you screenshotted, or
how you cropped. Rotating selectors would buy nothing and cost accuracy.

**Visible to them.** Request cadence, device fingerprint, IP, and account behaviour. The
app embeds analytics SDKs, so interaction telemetry does leave the device; mechanically
regular scrolling and a job firing at exactly the same second daily are the realistic
tells. That is what the `human` block randomises — scroll distances, dwell times, swipe
durations, tap coordinates, gaps between cities, and visiting order, with occasional
longer pauses. Set `human.enabled: false` for deterministic debugging.

**Detectable on-device.** An app can notice an active accessibility service, and
`uiautomator2` installs a helper APK. Unlikely to matter here, and selector choice cannot
help with it either way.

Practical risk is an account ban, so use a burner number.

## Setup

You need a running Android instance for **every** capture, not just discovery — banners
only exist as rendered pixels. It does not have to be a physical phone.

**Emulator (recommended, and confirmed working).** An Android Studio AVD with a Play Store
system image. Blinkit installs, logs in, and runs normally under emulation. The advantage
over a phone is `adb emu geo fix`, which sets GPS from the command line and avoids the
fragile in-app address picker. Create the AVD in Device Manager, install Blinkit from the
Play Store, log in with a burner number, and set a delivery address.

**Physical phone (fallback).** Android 9+, Developer options unlocked, USB debugging on.
Set `location_mode: ui` or `manual`, since `adb emu` only works on emulators. Nothing else
changes — crops come from hierarchy bounds, not fixed pixels, so tuning transfers across
screen resolutions.

`adb` does not need to be on PATH; it is located automatically under the Android SDK.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Commands

```powershell
$py = ".\.venv\Scripts\python.exe"

$py -m blinkit_banners.cli doctor         # device reachable? app installed? emulator?
$py -m blinkit_banners.cli discover       # dump the current screen and show what we detect
$py -m blinkit_banners.cli run            # sweep four cities, OCR new banners, refresh dashboard
$py -m blinkit_banners.cli report         # library contents with sizes and aspects
$py -m blinkit_banners.cli sheet          # render the library as one contact sheet
$py -m blinkit_banners.cli dedupe-check   # closest pairs, for tuning dedupe_distance
$py -m blinkit_banners.cli analyse        # read copy and attributes out of every creative
$py -m blinkit_banners.cli dashboard      # write data/dashboard.html
```

Start with `run --skip-location` to capture wherever the device already is. Add `--seed N`
to make a run's jitter reproducible while testing. Drop `--skip-location` once you want it
cycling through Delhi, Mumbai, Chennai and Hyderabad. Each city has one busy pincode in
`config.yaml` (Connaught Place 110001, Bandra West 400050, T. Nagar 600017, Banjara Hills
500034). `run` hashes every crop against the library: a match records another sighting
(city, pincode, time) against the existing creative; only a new image is saved. After the
sweep it OCRs any new files and rewrites `data/dashboard.html`.

`discover` writes `data/discovery/<timestamp>/` with the raw hierarchy, a screenshot, and
`annotated.png` showing red boxes over detected banners and blue over carousels. Always
check that image after an app update.

## How the home feed is laid out

Findings from a real device, current as of the last update. Everything here is config, so
re-run `discover` and adjust if Blinkit reworks the feed.

| Element | Selector | Size |
| --- | --- | --- |
| Hero banner | `ImageView` id `image_view`, desc `Promotional banner` | 1024x716 |
| Hero carousel | `ViewPager` id `view_pager_2` | 1280x716 |
| Promo tiles | `ImageView` id `image` in `horizontal_recycler_view` | 586x415 |
| Sticky search + category rail | `sticky_header` | top 636px |
| Bottom nav | `bottom_nav_bar_container` | bottom 258px |

Detection runs two passes and merges them. The **shape pass** takes wide, roughly
landscape views in the body of the feed. The **id pass** takes anything matching
`resource_id_allow` or `content_desc_allow` on looser geometry, since a view you have
named is worth trusting even if a redesign reshapes it. Where both find the same pixels,
the id match wins. So filling in real IDs sharpens detection without making it brittle —
if an update renames them, the shape pass still catches them and `discover` reports them
as `shape` so you know to refresh. `strict_ids: true` trusts the allowlist alone.

Resource IDs are matched **exactly** on the last path segment, because substring matching
is unsafe here: `image_view` is the hero banner but `image_view_primary` is a product
thumbnail.

## Three traps this code works around

**Empty layout views.** Blinkit lays childless `ViewGroup`s over the feed as decoration —
`bottom_clip_lottie_image_view` over the category grid, `strip_container` in the sticky
footer. They are banner-shaped and have no children, so a naive "leaf node = image" rule
crops them, capturing whatever renders underneath. Seven category-grid crops got into an
early library this way. Layout classes are now never treated as images regardless of
whether they have children.

**Scrolling past banners.** A banner is only captured once wholly inside the visible band,
which is `1 - exclude_top - exclude_bottom` of the screen. If a scroll advances further
than the band can accommodate beyond the banner's own height, content moves from below the
band to above it without ever being fully visible. Scrolling is capped at
`band - max_banner_height_ratio`. Getting this wrong is quiet and expensive: an early run
scrolled 0.55-0.80 per step against a 0.67 band and captured 4 creatives where the correct
cap captures 17.

**Interstitials.** A notification opt-in dialog appears on some relaunches and covers the
feed. Each capture is checked for a "No, thanks"-style control and dismissed before
ingest. Add wording to `DISMISS_TEXT` in `flows.py` if you meet a new one.

## Carousels

Exhaustion swipes until the carousel comes **back round to the slide it started on**.
Two things make that harder than it sounds.

The carousel auto-rotates on its own timer, so a slow loop lets it advance underneath us —
which skips slides and makes a repeat look like the end of the rotation. Most of that
slowness was the hierarchy dump inside `capture`. The slide rectangle is now resolved once
from the hierarchy and every subsequent frame is a screenshot-only crop of that same
rectangle, roughly tripling loop speed so it outruns the rotation.

Staleness is also judged run-wide rather than per visit. The hero carousel stays fully
visible across about six consecutive scroll steps, so it gets re-walked at each; counting
slides already recorded anywhere in this run means a repeat visit costs a couple of swipes
instead of a full lap. `carousel_stall_limit` is the backstop for a carousel that never
cleanly returns to its first slide, and `carousel_max_slides` caps one lap.

Getting this right roughly doubled the yield: an earlier build stopped after ~2 swipes per
visit and collected 17 creatives; full-lap exhaustion collects 28 from the same feed.

## Gesture volume

A one-location sweep measures at **20 feed scrolls, 26 carousel swipes, 26 full captures**
plus screenshot-only reads, over about six minutes.

Runtime is dominated by carousel laps, so `feed.scroll_steps` and `carousel_max_slides`
are the two levers if you need it shorter. Every run prints its own gesture and read
counts, so tune against measurements rather than guesses.

## Deduplication

`capture.dedupe_distance` is the maximum Hamming distance between 256-bit perceptual
hashes still treated as one creative. Measured on real captures: two renders of the same
creative differ by **14 bits**, while the closest genuinely different pair differs by
**74**. The default of 32 sits in that gap with wide margin. Use `dedupe-check` to
re-measure after an app update — if a pair that looks identical appears above the
threshold line, raise it.

## Output

```
data/
  creatives/<phash>.png    one file per unique creative
  screens/                 full screenshots, for auditing crops
  banners.db               SQLite
  contact_sheet.png        sheet output
  dashboard.html           browsable, filterable creative library
  discovery/               discover output
```

`creatives` is the deduplicated library, one row per distinct image with first/last seen
timestamps. `sightings` records every observation — creative, location, surface, slot
position, timestamp. That split is what lets you ask when a banner started running in a
given city.

## Reading the creatives

Copy is not stored in the source. Every new creative is OCRed when `run` finishes (or when
you call `analyse` yourself). Headlines, brands and occasions live in SQLite and are rebuilt
from the pixels, so a later scrape of a new banner goes through the same path automatically.
Brands the analyser has not seen before are learned from the wordmark or copy and reused on
the next run; `taxonomy.yaml` is only a seed for aliases and categories.

`analyse` can still be run by hand. It is fully offline: RapidOCR on CPU, a dictionary, and
some geometry. No API key, no GPU, no network.

The pipeline is four steps.

**OCR** returns each line of text with its box, cap height, confidence, and skew angle.

**Layout** turns those lines into a headline, subheadline, CTA and Ad badge. Blinkit lays its
creatives out consistently — copy left-aligned in a narrow column, product photography to the
right — and the classifier leans on that. Two rules earn their keep. The copy column is the
one holding the *tallest* line, not the most text, because a stack of tiny labels on a shampoo
bottle would otherwise outvote a real headline. And lines rotated more than a few degrees are
discarded, because a tilted gift card gets an inflated bounding box that can look bigger than
the headline. Within the column, lines split into paragraphs on vertical gap *or* a change in
font size; the gap alone merges "Keep the party going with Lay's" into the line beneath it.

**Taxonomy** resolves brand, category and occasion against `taxonomy.yaml`. Matching is fuzzy
and space-insensitive, so OCR slips like `Oadbury`, `SUPERYOV` and `Vembleyrange` still land.
Brand carries a source, because the evidence differs in strength:

| Source | Confidence | Meaning |
| --- | --- | --- |
| `logo` | 0.95 | wordmark in the top-right corner, i.e. the advertiser |
| `copy` | 0.85 | named in the headline or subheadline |
| `product` | 0.50 | only legible on a pack in the artwork |

That last tier matters. A "Chocolate Paradise" category banner shows Cadbury and Ferrero packs
without being an ad for either, so those are recorded but flagged weak and greyed out in the
dashboard.

**Visual** samples the left margin strip — inboard of the rounded corners, outboard of the
text — and calls the background flat, gradient or photo.

One more wrinkle: OCR regularly drops the space between words, giving `Keepthe party` and
`Getsupplements,massagers`. Those are repaired with dictionary splitting, guarded so it never
touches a name in `taxonomy.yaml`. Without the guard, `Saffola` becomes `S a ff ola`.

### Extending it

`taxonomy.yaml` is the whole knowledge base — brands with their aliases and category, category
keywords, occasion keywords. Add entries as you meet new advertisers; nothing is hard-coded.
Anything you add is also protected from word-splitting automatically.

### Why there is no vision model

On the first 28 creatives this resolves a brand on 23. The five misses are all Blinkit's own
first-party banners ("Spotless home, fresh clothes", "Gifts they'll truly love"), which
advertise no brand at all, so a blank is the right answer. Every advertiser creative resolved.

That works because Blinkit writes the brand into the copy or shows a wordmark that OCR reads,
and states the occasion outright ("Enjoy the monsoon", "The perfect Rakhi gifts start here").
A local VLM small enough for a 4GB card would be *worse* at exactly the mid-tier Indian brands
in this set. Revisit only if logo-only creatives start showing up in volume.

## Location modes

The in-app change, as of the current Blinkit build:

1. Tap the address line under the delivery-time header (the line with the pincode).
2. Type the area or pincode into **Search for area, street name...**
3. Tap the first suggestion card.
4. On the map, tap **Confirm Location** — not the small **Change** link.
5. If a promo lands on home (Ambulance and similar), tap the **X**. Layout of that
   popup changes by city, so dismissal is by Close control, not by copy.

Set `location_mode`, or override per run with `--location-mode`.

`ui` is the default and follows the steps above. `geo` sets emulator GPS via
`adb emu geo fix`, then taps **Use current location** and still confirms the map.
`manual` captures wherever the device already is.

The dashboard shows each creative's cities, pincodes and scrape dates (IST), and filters by
them. The page is a white static HTML file at `data/dashboard.html`.

## Scheduling

Use Task Scheduler to fire `run` twice a day, and widen `human.start_jitter` to something
like `[0, 1800]` so it does not begin at the same second each time.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest tests -q
```

Tests covering detection, the empty-overlay and occlusion rules, the scroll cap,
interstitial matching, dedupe, config parsing, and jitter. `test_carousel.py` drives
exhaustion against a simulated pager — a ring of slides that advances on swipe and
optionally on its own timer — to check that a lap ends on returning to the first slide,
survives auto-rotation, and stays cheap on revisits. All run against synthetic screens, so
no device is needed.
