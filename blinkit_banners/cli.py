from __future__ import annotations

import math
import sys
from datetime import datetime
from pathlib import Path

import click
from PIL import Image, ImageDraw

from . import config as config_module
from .capture import Collector, purge_non_banners
from .config import Location
from .detect import find_banners, find_carousels
from .device import Device, DeviceError
from .flows import set_location, sweep_home
from .humanize import Human
from .store import Store

CONFIG_OPTION = click.option(
    "--config", "config_path", default="config.yaml", show_default=True, help="Path to the YAML config."
)


@click.group()
def cli() -> None:
    """Capture Blinkit app banner creatives via on-device screenshots."""


@cli.command()
@CONFIG_OPTION
def doctor(config_path: str) -> None:
    """Check that a device is reachable and the Blinkit app is installed."""
    cfg = config_module.load(config_path)
    try:
        device = Device(cfg.device)
    except DeviceError as exc:
        click.secho(str(exc), fg="red")
        sys.exit(1)

    width, height = device.window_size()
    click.echo(f"device      {device.serial}")
    click.echo(f"screen      {width}x{height}")
    click.echo(f"emulator    {'yes' if device.is_emulator else 'no'}")

    packages = device.d.app_list()
    installed = cfg.device.package in packages
    click.secho(f"app         {cfg.device.package} {'installed' if installed else 'NOT INSTALLED'}",
                fg="green" if installed else "red")
    click.echo(f"foreground  {device.d.app_current().get('package')}")

    if cfg.location_mode == "geo" and not device.is_emulator:
        click.secho(
            "warning     location_mode: geo needs an emulator; use 'ui' or 'manual' on a phone.",
            fg="yellow",
        )
    if not installed:
        sys.exit(1)


@cli.command()
@CONFIG_OPTION
@click.option("--launch", is_flag=True, help="Restart the app before inspecting.")
def discover(config_path: str, launch: bool) -> None:
    """Dump the current screen so banner selectors can be verified against reality.

    Writes the raw hierarchy, a screenshot, and an annotated screenshot showing
    what the current detection settings would capture.
    """
    cfg = config_module.load(config_path)
    device = Device(cfg.device)
    if launch:
        device.launch_app()

    out_dir = cfg.output_path / "discovery" / datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)

    screen = device.capture()
    (out_dir / "hierarchy.xml").write_text(device.d.dump_hierarchy(compressed=False), encoding="utf-8")
    screen.image.save(out_dir / "screenshot.png")

    carousels = find_carousels(screen.root, cfg.detection)
    candidates = find_banners(screen.root, cfg.detection)

    annotated = screen.image.copy()
    draw = ImageDraw.Draw(annotated)
    for node in carousels:
        draw.rectangle(node.bounds.scaled(screen.scale).as_tuple(), outline=(0, 128, 255), width=6)
    for index, candidate in enumerate(candidates):
        box = candidate.rect.scaled(screen.scale)
        draw.rectangle(box.as_tuple(), outline=(255, 0, 0), width=5)
        draw.text((box.left + 10, box.top + 10), str(index), fill=(255, 0, 0))
    annotated.save(out_dir / "annotated.png")

    click.echo(f"\n{len(carousels)} carousel(s), {len(candidates)} banner candidate(s)\n")
    for index, candidate in enumerate(candidates):
        source = "by-id " if candidate.from_id else "shape "
        marker = "carousel" if candidate.carousel else "static  "
        click.echo(f"  [{index}] {source} {marker}  {candidate.node.describe()}")

    click.echo(f"\nWrote {out_dir}")
    click.echo("Open annotated.png. If the red boxes are wrong, tune `detection` in the config")
    click.echo("or copy the real resource-ids from the listing above into resource_id_allow.")


@cli.command()
@CONFIG_OPTION
@click.option("--skip-location", is_flag=True, help="Capture the device's current location only.")
@click.option(
    "--location",
    "only",
    multiple=True,
    help="Run one named location from the config. Repeat the flag to pick several.",
)
@click.option("--location-mode", "mode_override", type=click.Choice(["geo", "ui", "manual"]), default=None)
@click.option("--seed", type=int, default=None, help="Fix the jitter RNG, for reproducible test runs.")
@click.option("--no-launch", is_flag=True, help="Do not restart the app; keep the screen that is already open.")
def run(config_path: str, skip_location: bool, only: tuple[str, ...], mode_override: str | None, seed: int | None, no_launch: bool) -> None:
    """Sweep the home feed and archive every banner creative found."""
    cfg = config_module.load(config_path)
    human = Human(cfg.human, seed=seed)
    device = Device(cfg.device, human=human)
    store = Store(cfg.output_path / "banners.db")
    collector = Collector(cfg, store, device_serial=device.serial)

    mode = mode_override or cfg.location_mode
    locations = cfg.locations
    if only:
        wanted = set(only)
        locations = [loc for loc in cfg.locations if loc.name in wanted]
        missing = wanted - {loc.name for loc in locations}
        if missing:
            known = ", ".join(loc.name for loc in cfg.locations) or "(none)"
            click.secho(
                f"Unknown location(s) {', '.join(sorted(missing))}. Known names: {known}",
                fg="red",
            )
            sys.exit(1)
    if skip_location or not locations:
        locations = [Location(name="current")]
        mode = "manual"

    human.sleep(cfg.human.start_jitter)
    if no_launch:
        device.ensure_foreground()
    else:
        device.launch_app()

    for index, location in enumerate(human.order(locations)):
        if index:
            human.sleep(cfg.human.location_gap)
        collector.reset_run()
        collector.place = location
        click.echo(f"\n=== {location.label} ===")
        try:
            set_location(device, location, mode)
            summary = sweep_home(device, collector, cfg, location.name)
        except Exception as exc:
            click.secho(f"  skipped: {exc}", fg="yellow")
            continue
        click.echo(f"  {summary.line()}")

    counts = device.counts
    click.echo(
        f"\ngestures: {counts['scroll']} feed scrolls, {counts['carousel_swipe']} carousel swipes, "
        f"{counts['tap']} taps"
        f"\nreads:    {counts['capture']} full captures, {counts['screenshot']} screenshots"
    )
    totals = store.stats()
    click.echo(f"library now holds {totals['creatives']} creatives across {totals['sightings']} sightings")
    click.echo(f"images: {cfg.output_path / 'creatives'}")

    from .analyse import analyse_pending
    from .dashboard import render

    result = analyse_pending(store)
    if result["pending"] == 0:
        click.echo("analyse: nothing new")
    else:
        click.echo(f"analyse: {result['analysed']} creatives, brand on {result['resolved']}")
        if result["learned"]:
            click.echo(f"learned {len(result['learned'])} brands: {', '.join(result['learned'])}")

    dropped = purge_non_banners(store, cfg.detection)
    if dropped:
        click.echo(f"dropped {dropped} non-banner crops")

    destination = cfg.output_path / "dashboard.html"
    render(store.library(), destination, store.place_summaries())
    click.echo(f"dashboard: {destination}")
    store.close()


@cli.command()
@CONFIG_OPTION
@click.option("--pairs", default=10, show_default=True, help="How many closest pairs to list.")
def dedupe_check(config_path: str, pairs: int) -> None:
    """List the most similar creative pairs, for tuning capture.dedupe_distance.

    Pairs below the configured distance were already merged, so anything listed
    here that looks like the same creative means the threshold is too tight.
    """
    cfg = config_module.load(config_path)
    db = cfg.output_path / "banners.db"
    if not Path(db).exists():
        click.secho("No database yet - run `run` first.", fg="yellow")
        return

    store = Store(db)
    hashes = [r["phash"] for r in store.conn.execute("SELECT phash FROM creatives ORDER BY first_seen")]
    store.close()

    distances = sorted(
        (bin(int(a, 16) ^ int(b, 16)).count("1"), i, j)
        for i, a in enumerate(hashes)
        for j, b in enumerate(hashes)
        if i < j and len(a) == len(b)
    )

    bits = len(hashes[0]) * 4 if hashes else 0
    click.echo(f"threshold {cfg.capture.dedupe_distance} of {bits} bits\n")
    for distance, i, j in distances[:pairs]:
        flag = "  <-- would merge" if distance <= cfg.capture.dedupe_distance else ""
        click.echo(f"  {distance:>3} bits   idx {i:>3} vs {j:>3}   {hashes[i][:12]} / {hashes[j][:12]}{flag}")


@cli.command()
@CONFIG_OPTION
@click.option("--limit", default=20, show_default=True)
def report(config_path: str, limit: int) -> None:
    """Show what is currently in the creative library."""
    cfg = config_module.load(config_path)
    db = cfg.output_path / "banners.db"
    if not Path(db).exists():
        click.secho("No database yet - run `run` first.", fg="yellow")
        return

    store = Store(db)
    totals = store.stats()
    click.echo(f"{totals['creatives']} creatives / {totals['sightings']} sightings\n")

    rows = store.conn.execute(
        "SELECT c.phash, c.width, c.height, c.sighting_count,"
        "       GROUP_CONCAT(DISTINCT s.surface) AS surfaces,"
        "       GROUP_CONCAT(DISTINCT s.location) AS locations"
        " FROM creatives c LEFT JOIN sightings s ON s.phash = c.phash"
        " GROUP BY c.phash ORDER BY c.first_seen LIMIT ?",
        (limit,),
    ).fetchall()

    click.echo(f"{'idx':>3}  {'hash':<12} {'size':<11} {'aspect':>6}  {'seen':>4}  surfaces")
    for index, row in enumerate(rows):
        size = f"{row['width']}x{row['height']}"
        aspect = row["height"] / row["width"] if row["width"] else 0
        click.echo(
            f"{index:>3}  {row['phash'][:12]} {size:<11} {aspect:>6.3f}  {row['sighting_count']:>4}  "
            f"{row['surfaces'] or ''}"
        )
    store.close()


@cli.command()
@CONFIG_OPTION
@click.option("--columns", default=4, show_default=True)
@click.option("--width", "cell_width", default=420, show_default=True, help="Thumbnail width in px.")
def sheet(config_path: str, columns: int, cell_width: int) -> None:
    """Render the whole creative library as one contact sheet for eyeballing."""
    cfg = config_module.load(config_path)
    db = cfg.output_path / "banners.db"
    if not Path(db).exists():
        click.secho("No database yet - run `run` first.", fg="yellow")
        return

    store = Store(db)
    rows = store.conn.execute(
        "SELECT c.phash, c.file_path, c.sighting_count,"
        "       (SELECT GROUP_CONCAT(DISTINCT s.surface) FROM sightings s WHERE s.phash = c.phash) AS surfaces"
        " FROM creatives c ORDER BY c.first_seen"
    ).fetchall()
    store.close()

    thumbs = []
    for row in rows:
        path = Path(row["file_path"])
        if not path.exists():
            continue
        img = Image.open(path).convert("RGB")
        height = max(1, round(img.height * cell_width / img.width))
        thumbs.append((img.resize((cell_width, height), Image.LANCZOS), row))

    if not thumbs:
        click.secho("No creative images on disk.", fg="yellow")
        return

    label_height = 34
    cell_height = max(t.height for t, _ in thumbs) + label_height
    rows_needed = math.ceil(len(thumbs) / columns)
    sheet_image = Image.new("RGB", (columns * cell_width, rows_needed * cell_height), (24, 24, 24))
    draw = ImageDraw.Draw(sheet_image)

    for index, (thumb, row) in enumerate(thumbs):
        x = (index % columns) * cell_width
        y = (index // columns) * cell_height
        sheet_image.paste(thumb, (x, y))
        surfaces = (row["surfaces"] or "").replace("home-carousel", "car")
        draw.text((x + 8, y + thumb.height + 9),
                  f"{index:>2} {row['phash'][:10]}  x{row['sighting_count']}  {surfaces[:44]}",
                  fill=(220, 220, 220))

    out = cfg.output_path / "contact_sheet.png"
    sheet_image.save(out)
    click.echo(f"{len(thumbs)} creatives -> {out}")


@cli.command()
@CONFIG_OPTION
@click.option("--taxonomy", "taxonomy_path", default="taxonomy.yaml", show_default=True)
@click.option("--force", is_flag=True, help="Re-analyse creatives that already have attributes.")
def analyse(config_path: str, taxonomy_path: str, force: bool) -> None:
    """Read copy and attributes out of every stored creative. Runs fully offline."""
    from .analyse import analyse_pending

    cfg = config_module.load(config_path)
    store = Store(cfg.output_path / "banners.db")
    result = analyse_pending(
        store,
        taxonomy_path,
        force=force,
        progress=lambda rows: click.progressbar(rows, label=f"Analysing {len(rows)} creatives"),
    )
    if result["pending"] == 0:
        click.echo("Nothing to analyse; pass --force to redo them.")
        store.close()
        return

    click.echo(f"analysed {result['analysed']}  brand resolved on {result['resolved']}")
    if result["learned"]:
        click.echo(f"learned {len(result['learned'])} new brands: {', '.join(result['learned'])}")
    store.close()


@cli.command()
@CONFIG_OPTION
@click.option("--output", "output_name", default="dashboard.html", show_default=True)
def dashboard(config_path: str, output_name: str) -> None:
    """Write a self-contained, filterable HTML view of the creative library."""
    from .dashboard import render

    cfg = config_module.load(config_path)
    store = Store(cfg.output_path / "banners.db")
    rows = store.library()
    destination = cfg.output_path / output_name
    render(rows, destination, store.place_summaries())
    store.close()
    click.echo(f"{len(rows)} creatives -> {destination}")


if __name__ == "__main__":
    cli()
