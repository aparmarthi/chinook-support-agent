"""Render the five demo slides to PDF and PNG.

    python scripts/render_slides.py

`docs/slides/deck.html` is the source of truth — it is the thing presented, and
it carries the presenter notes. This produces the artifacts that survive a dead
laptop, a broken projector, or a submission that has to be read rather than
watched: `reports/slides/deck.pdf` and one PNG per slide.

Regenerate after editing the deck. A stale PNG of a corrected number is exactly
the failure the rest of this repo is about, so these are generated rather than
exported by hand.

Chromium comes from the Playwright cache if it is there. No new dependency: the
browser is already on disk and driving it through a library would add one to
render two static pages.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DECK = ROOT / "docs" / "slides" / "deck.html"
OUT = ROOT / "reports" / "slides"
SLIDES = 5
VIEWPORT = (1280, 720)

CANDIDATES = (
    "chromium_headless_shell-*/chrome-headless-shell-*/chrome-headless-shell",
    "chromium-*/chrome-mac*/Chromium.app/Contents/MacOS/Chromium",
)


def find_chrome() -> Path:
    """Locate a headless Chromium, preferring the Playwright cache.

    Raises:
        SystemExit: If no browser binary is found.
    """
    cache = Path.home() / "Library" / "Caches" / "ms-playwright"
    for pattern in CANDIDATES:
        for path in sorted(cache.glob(pattern), reverse=True):
            if path.is_file():
                return path
    raise SystemExit(
        "no headless Chromium found. Install one with `playwright install "
        "chromium`, or pass a binary as the first argument."
    )


def run(chrome: Path, *args: str) -> None:
    result = subprocess.run(
        [str(chrome), "--disable-gpu", "--no-sandbox", *args],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"chromium failed:\n{result.stderr[-2000:]}")


def main() -> None:
    chrome = Path(sys.argv[1]) if len(sys.argv) > 1 else find_chrome()
    OUT.mkdir(parents=True, exist_ok=True)
    url = DECK.as_uri()

    pdf = OUT / "deck.pdf"
    run(chrome, "--no-pdf-header-footer", f"--print-to-pdf={pdf}", url)
    print(f"  {pdf.relative_to(ROOT)}")

    # The deck reads its starting slide from the hash, so each page is a
    # separate load rather than a keypress we would have to script.
    for n in range(1, SLIDES + 1):
        png = OUT / f"slide-{n}.png"
        run(
            chrome,
            f"--screenshot={png}",
            f"--window-size={VIEWPORT[0]},{VIEWPORT[1]}",
            f"{url}#{n}",
        )
        print(f"  {png.relative_to(ROOT)}")

    print(f"\nrendered {SLIDES} slides from {DECK.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
