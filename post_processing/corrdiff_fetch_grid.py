"""Download the CorrDiff output grid files.

``corrdiff_to_zarr.py`` and ``corrdiff_to_latlon_zarr.py`` both need
``corrdiff_output_lat.npy`` / ``corrdiff_output_lon.npy`` (~15 MB each) in
``--grid-dir`` before they can run. These are the CorrDiff US GEFS->HRRR
model's fixed native curvilinear output grid (``earth2-corrdiff-us-gefs-hrrr:1.0.0``)
-- identical for every deployment, not specific to any user's NIM -- so
they're published once as GitHub Release assets rather than requiring cluster
access to fetch. This script pulls them down with a plain HTTPS GET, no auth,
no git, no Kubernetes.

Usage (installed, see this folder's pyproject.toml):
    corrdiff-fetch-grid --grid-dir /path/to/grid

Or standalone:
    python corrdiff_fetch_grid.py

Re-run any time to (re-)download -- e.g. after a new grid release is
published (``--release`` picks a different tag).
"""

from __future__ import annotations

import argparse
from pathlib import Path

import requests

DEFAULT_GRID_DIR = Path(__file__).resolve().parent

REPO = "xlaurahu/CorrDiffSCIL"
DEFAULT_RELEASE = "grid-v1"
GRID_FILES = ["corrdiff_output_lat.npy", "corrdiff_output_lon.npy"]


def asset_url(filename: str, release: str = DEFAULT_RELEASE) -> str:
    """Return the download URL for a grid file in a given Release tag."""
    return f"https://github.com/{REPO}/releases/download/{release}/{filename}"


def fetch_grid(
    grid_dir: str | Path = DEFAULT_GRID_DIR,
    release: str = DEFAULT_RELEASE,
    force: bool = False,
    timeout: int = 120,
) -> list[Path]:
    """Download both grid files into ``grid_dir``. Returns the written paths.

    Skips a file that already exists unless ``force`` is set.
    """
    grid_dir = Path(grid_dir)
    grid_dir.mkdir(parents=True, exist_ok=True)

    written = []
    for filename in GRID_FILES:
        dest = grid_dir / filename
        if dest.exists() and not force:
            print(f"Already have {dest} (use --force to re-download).")
            written.append(dest)
            continue

        url = asset_url(filename, release)
        print(f"Fetching {url} ...")
        r = requests.get(url, timeout=timeout, stream=True)
        if r.status_code != 200:
            raise RuntimeError(f"Download failed ({r.status_code}): {url}")

        tmp = dest.with_suffix(dest.suffix + ".part")
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)
        tmp.rename(dest)
        print(f"  wrote {dest} ({dest.stat().st_size / 1e6:.1f} MB)")
        written.append(dest)

    return written


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--grid-dir", default=str(DEFAULT_GRID_DIR),
                        help=f"Where to save the grid files (default: {DEFAULT_GRID_DIR}).")
    parser.add_argument("--release", default=DEFAULT_RELEASE,
                        help=f"GitHub Release tag to fetch from (default: {DEFAULT_RELEASE}).")
    parser.add_argument("--force", action="store_true",
                        help="Re-download even if the files already exist.")
    args = parser.parse_args(argv)

    fetch_grid(grid_dir=args.grid_dir, release=args.release, force=args.force)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
