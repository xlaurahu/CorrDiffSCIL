"""CorrDiff prediction backend.

Automates the CorrDiff prediction workflow that previously lived in
``OhioRiverFloodLoop.ipynb`` so it can be driven from a website/back end
instead of pasting cells into a notebook every time.

The module is split into two layers:

* **Reusable functions** (``check_nim_health``, ``setup_data_sources``,
  ``fetch_input_gefs``, ``run_prediction``) — call these directly from a web
  back end / job runner. They take plain arguments and return values; none of
  them prompt or print interactively.
* **Interactive CLI** (``main``) — reproduces the manual flow: it asks for the
  NIM username and runs a health check, sets up the model/data sources, then
  asks for the initial-condition date, forecast hours, sample count and step
  count, and runs the prediction.

Output files (per date, in ``<output_root>/<YYYY-MM-DD>/``):

    {YYYY-MM-DD}_H{H:03d}_sample{i:02d}.npy   # one 2-D precip field per sample
    {YYYY-MM-DD}_H{H:03d}_ensemble_mean.npy   # mean over samples

These are exactly the arrays the post-processing / MRMS-validation step
consumes, so downstream analysis needs no manual edits.
"""

from __future__ import annotations

import argparse
import io
import os
import tarfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np
import requests

# --------------------------------------------------------------------------- #
# Constants (copied verbatim from the notebook so results match exactly)
# --------------------------------------------------------------------------- #

GEFS_SELECT_VARIABLES = [
    "u10m",
    "v10m",
    "t2m",
    "r2m",
    "sp",
    "msl",
    "tcwv",
]

GEFS_VARIABLES = [
    "u1000", "u925", "u850", "u700", "u500", "u250",
    "v1000", "v925", "v850", "v700", "v500", "v250",
    "z1000", "z925", "z850", "z700", "z500", "z200",
    "t1000", "t925", "t850", "t700", "t500", "t100",
    "r1000", "r925", "r850", "r700", "r500", "r100",
]

# Output variables produced by the CorrDiff model, in channel order. A sample's
# 2-D field for variable v is sample_data[0, 0, CORRDIFF_VARIABLES.index(v)].
#   u10m, v10m  : 10 m wind components
#   t2m         : 2 m temperature
#   tp          : total precipitation accumulated over the PREVIOUS 1 hour (mm),
#                 NOT a rate. The model only emits every 3 h, so a 3-hour total
#                 is estimated downstream as tp*3 (assuming the two unobserved
#                 intervening hours matched the reported hour). The script saves
#                 the raw 1-hour tp field; it does not apply the *3.
#   csnow, cicep, cfrzr, crain : categorical snow / ice pellets / freezing rain / rain
CORRDIFF_VARIABLES = [
    "u10m", "v10m", "t2m", "tp", "csnow", "cicep", "cfrzr", "crain",
]
CORRDIFF_CHANNEL = {name: i for i, name in enumerate(CORRDIFF_VARIABLES)}

# Default variable(s) to save if the caller doesn't specify any.
DEFAULT_VARIABLES = ["tp"]

# Ensemble percentiles saved per variable/hour (across the samples).
DEFAULT_PERCENTILES = [10, 25, 50, 75, 90]

# CorrDiff US (GEFS -> HRRR) domain crop, bounding box [225, 21, 300, 53].
_LAT_SLICE = slice(148, 277)   # 129 rows
_LON_SLICE = slice(900, 1201)  # 301 cols
_CROP_SHAPE = (129, 301)

# Every NIM this package talks to is reached through its owner's public
# Kubernetes Ingress -- a per-user HTTPS hostname, reachable from anywhere
# (no cluster access, no kubeconfig, no GPU needed on the caller's side). This
# is deliberately the *only* built-in way to locate a NIM: a user integrating
# this package into their own forecasting system just needs someone's
# username, nothing Kubernetes-shaped to configure. It has NO built-in
# authentication -- anyone with the URL can submit inference requests against
# that GPU, so only use a username whose NIM you trust, and only stand up
# your own Ingress if you're prepared to share the URL that broadly. See
# corrdiff-nim-ingress.yaml.
PUBLIC_INGRESS_TEMPLATE = "https://corrdiff-{username}.nrp-nautilus.io"

# Max forecast lead the CorrDiff model produces here.
MAX_FORECAST_HOUR = 24


# --------------------------------------------------------------------------- #
# NIM service helpers
# --------------------------------------------------------------------------- #

def nim_base_url(username: str | None = None, host: str | None = None) -> str:
    """Return the base URL of the CorrDiff NIM service.

    Resolution order:
      1. ``host`` (or the ``CORRDIFF_NIM_HOST`` env var), if given -- used
         verbatim. Accepts a bare ``host[:port]`` or a full ``http(s)://...``
         URL. An escape hatch for a NIM reached some other way; most users
         won't need it.
      2. Otherwise, ``username``'s public Ingress URL
         (``https://corrdiff-<username>.nrp-nautilus.io``) -- the normal way
         to reach any NIM this package knows how to find.
    """
    host = host or os.environ.get("CORRDIFF_NIM_HOST")
    if host:
        return host if "://" in host else f"http://{host}"
    if not username:
        raise ValueError(
            "Need either a host (CORRDIFF_NIM_HOST / --nim-host) or a "
            "username (for the public Ingress)."
        )
    return PUBLIC_INGRESS_TEMPLATE.format(username=username)


def check_nim_health(username: str | None = None, timeout: int = 30, host: str | None = None) -> bool:
    """Check the CorrDiff NIM ``/v1/health/ready`` endpoint.

    Returns True if the service reports ready (HTTP 200), else False. Prints a
    short status so it is useful both interactively and in job logs. Network
    errors are caught and reported as "not ready" rather than raising.
    """
    url = f"{nim_base_url(username, host)}/v1/health/ready"
    try:
        r = requests.get(url, timeout=timeout)
    except requests.RequestException as exc:
        print("NIM is not reachable!")
        print("URL:", url)
        print("Error:", exc)
        return False

    if r.status_code == 200:
        print("NIM is healthy!")
        print("Response:", r.text)
        return True

    print("NIM is not ready!")
    print("Status:", r.status_code)
    print("Response:", r.text)
    return False


# --------------------------------------------------------------------------- #
# Model / data-source setup
# --------------------------------------------------------------------------- #

def patch_aiobotocore_session_close() -> bool:
    """Work around aiobotocore's "Session was never entered" teardown error.

    earth2studio's GEFS backend fetches the grib data successfully, then calls
    ``session.close()`` on an aiobotocore session it obtained via
    ``s3fs.set_session()`` without entering it as an async context manager.
    Some aiobotocore versions assert the session was entered first, so the
    close raises ``AssertionError: Session was never entered`` *after* the data
    is downloaded — the fetch never returns even though it worked.

    This makes ``AIOHTTPSession.__aexit__`` a no-op when the session was never
    entered (nothing to tear down in that case). Idempotent; returns True if
    the patch is in place, False if aiobotocore isn't importable.
    """
    try:
        from aiobotocore.httpsession import AIOHTTPSession
    except Exception:
        return False

    if getattr(AIOHTTPSession, "_e2s_close_patched", False):
        return True

    orig_aexit = AIOHTTPSession.__aexit__

    async def _safe_aexit(self, exc_type, exc_val, exc_tb):
        # _sessions is populated in __aenter__; if it's missing the session was
        # never entered, so there is nothing to close.
        if getattr(self, "_sessions", None) is None:
            return None
        return await orig_aexit(self, exc_type, exc_val, exc_tb)

    AIOHTTPSession.__aexit__ = _safe_aexit
    AIOHTTPSession._e2s_close_patched = True
    return True


def patch_windows_gefs_s3_keys() -> bool:
    """Work around earth2studio building GEFS S3 keys with backslashes on Windows.

    earth2studio's GEFS source builds S3 object keys with ``os.path.join``,
    which uses ``\\`` on Windows -- producing a key like
    ``noaa-gefs-pds\\gefs.20240926/00\\atmos\\...`` that doesn't exist in the
    bucket, so every fetch 404s with ``FileNotFoundError``. This is a genuine
    upstream bug (S3 keys are always ``/``-separated, regardless of OS), not
    anything specific to this package.

    Makes the module's own ``os`` reference use ``posixpath`` for path joining
    while still delegating every other ``os.*`` attribute (``environ``,
    ``getenv``, etc.) to the real module, so nothing else in earth2studio's
    GEFS code is affected. No-op (and a no-op return of ``False``) on
    Mac/Linux, where ``os.path.join`` already produces forward slashes.
    Idempotent; safe to call more than once.
    """
    if os.name != "nt":
        return False

    try:
        import earth2studio.data.gefs as _gefs_module
    except Exception:
        return False

    if getattr(_gefs_module, "_e2s_windows_path_patched", False):
        return True

    import posixpath

    class _PosixOSProxy:
        path = posixpath

        def __getattr__(self, name):
            return getattr(os, name)

    _gefs_module.os = _PosixOSProxy()
    _gefs_module._e2s_windows_path_patched = True
    return True


def setup_data_sources(cache: bool = True, member: str = "gec00"):
    """Create the GEFS data sources used to build CorrDiff inputs.

    Imports ``earth2studio`` lazily so the module can be imported (and its
    pure-Python helpers used/tested) on a machine without the full
    earth2studio stack installed. Returns ``(ds_gefs, ds_gefs_select)``.

    Also applies :func:`patch_aiobotocore_session_close` so the GEFS S3 fetches
    don't crash on session teardown, and (Windows only)
    :func:`patch_windows_gefs_s3_keys` so those fetches don't 404 on a
    backslash-corrupted S3 key.
    """
    patch_aiobotocore_session_close()
    patch_windows_gefs_s3_keys()

    from earth2studio.data import GEFS_FX, GEFS_FX_721x1440

    ds_gefs = GEFS_FX(cache=cache)
    ds_gefs_select = GEFS_FX_721x1440(cache=cache, member=member)
    return ds_gefs, ds_gefs_select


# --------------------------------------------------------------------------- #
# Input construction
# --------------------------------------------------------------------------- #

# earth2studio's GEFS S3 backend intermittently raises during async session
# teardown (e.g. aiobotocore "AssertionError: Session was never entered") even
# though the data itself is reachable. These failures are transient, so retry.
GEFS_FETCH_ATTEMPTS = 4
GEFS_FETCH_RETRY_DELAY = 5.0  # seconds between attempts


def _fetch_with_retry(source, valid_time, lead_time, variables, what):
    """Call an earth2studio data source, retrying transient S3/async errors."""
    last_exc = None
    for attempt in range(1, GEFS_FETCH_ATTEMPTS + 1):
        try:
            return source(valid_time, lead_time, variables)
        except Exception as exc:  # noqa: BLE001 - upstream raises many types
            last_exc = exc
            print(
                f"  [{what}] fetch attempt {attempt}/{GEFS_FETCH_ATTEMPTS} failed: "
                f"{type(exc).__name__}: {exc}"
            )
            if attempt < GEFS_FETCH_ATTEMPTS:
                time.sleep(GEFS_FETCH_RETRY_DELAY)
    raise RuntimeError(
        f"{what} GEFS fetch failed after {GEFS_FETCH_ATTEMPTS} attempts"
    ) from last_exc


def fetch_input_gefs(
    ds_gefs,
    ds_gefs_select,
    time: datetime,
    lead_time: timedelta,
    content_dtype: str = "float32",
) -> np.ndarray:
    """Fetch GEFS data and assemble the single CorrDiff input array.

    Mirrors the notebook's ``fetch_input_gefs`` but takes the data sources as
    arguments (so they are created once via :func:`setup_data_sources`).
    """
    import torch  # lazy: only needed when actually building inputs

    dtype = np.dtype(getattr(np, content_dtype))
    g = np.array(9.80665, dtype=dtype)  # standard gravity (m/s^2)

    # High-res "select" surface fields, cropped to the CorrDiff domain.
    select_data = _fetch_with_retry(
        ds_gefs_select, time, lead_time, GEFS_SELECT_VARIABLES, "select"
    ).values
    select_data = select_data[:, 0, :, _LAT_SLICE, _LON_SLICE].astype(dtype)
    assert select_data.shape == (1, len(GEFS_SELECT_VARIABLES), *_CROP_SHAPE)

    # Pressure-level fields, interpolated to the 0.25 deg grid then cropped.
    pressure_data = _fetch_with_retry(
        ds_gefs, time, lead_time, GEFS_VARIABLES, "pressure"
    )
    pressure_data = torch.nn.functional.interpolate(
        torch.as_tensor(pressure_data.values),
        (len(GEFS_VARIABLES), 721, 1440),
        mode="nearest",
    ).numpy()
    pressure_data = pressure_data[:, 0, :, _LAT_SLICE, _LON_SLICE].astype(dtype)
    assert pressure_data.shape == (1, len(GEFS_VARIABLES), *_CROP_SHAPE)

    # Geopotential (m^2/s^2) -> geopotential height (m).
    z_vars = {"z1000", "z925", "z850", "z700", "z500", "z200"}
    z_indices = [i for i, v in enumerate(GEFS_VARIABLES) if v in z_vars]
    pressure_data[:, z_indices, :, :] /= g

    # Lead-time field, expressed in 3-hour increments.
    lead_hour = int(lead_time.total_seconds() // (3 * 60 * 60)) * np.ones(
        (1, 1, *_CROP_SHAPE), dtype=dtype
    )

    input_data = np.concatenate([select_data, pressure_data, lead_hour], axis=1)[None]
    return input_data


# --------------------------------------------------------------------------- #
# Inference
# --------------------------------------------------------------------------- #

def infer_corrdiff(
    username: str | None,
    input_array: np.ndarray,
    samples: int,
    steps: int,
    seed: int = 0,
    timeout: int = 3000,
    host: str | None = None,
) -> list[np.ndarray]:
    """POST one input array to the NIM and return the list of sample arrays.

    Each returned element is a full CorrDiff output sample (as stored in the
    response tar); the caller extracts the precip channel. Writes the request
    payload to a temp file because the NIM expects a multipart ``.npy`` upload.

    ``host`` selects which NIM to hit -- see ``nim_base_url()``.
    """
    url = f"{nim_base_url(username, host)}/v1/infer"
    headers = {"accept": "application/x-tar"}
    data = {"samples": samples, "steps": steps, "seed": seed}

    buf = io.BytesIO()
    np.save(buf, input_array)
    buf.seek(0)
    files = {"input_array": ("input_array", buf)}

    r = requests.post(url, headers=headers, data=data, files=files, timeout=timeout)
    if r.status_code != 200:
        raise RuntimeError(f"NIM inference failed ({r.status_code}): {r.content!r}")

    samples_out: list[np.ndarray] = []
    with tarfile.open(fileobj=io.BytesIO(r.content)) as tar:
        for member in tar.getmembers():
            arr_file = io.BytesIO(tar.extractfile(member).read())
            arr_file.seek(0)
            samples_out.append(np.load(arr_file))
    return samples_out


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #

def date_str(year: int, month: int, day: int) -> str:
    """Return the ``YYYY-MM-DD`` string used in output filenames/folders."""
    return f"{year:04d}-{month:02d}-{day:02d}"


def validate_hours(hours: Iterable[int]) -> list[int]:
    """Sort/validate forecast hours; each must be in 1..MAX_FORECAST_HOUR."""
    hours = sorted({int(h) for h in hours})
    if not hours:
        raise ValueError("No forecast hours given.")
    bad = [h for h in hours if h < 1 or h > MAX_FORECAST_HOUR]
    if bad:
        raise ValueError(
            f"Forecast hours must be between 1 and {MAX_FORECAST_HOUR}; got {bad}."
        )
    return hours


def validate_variables(variables: Iterable[str]) -> list[str]:
    """Validate requested output variables and return them in channel order.

    Names are matched case-insensitively against ``CORRDIFF_VARIABLES``.
    Duplicates are dropped; the result is ordered by CorrDiff channel index so
    downstream code sees a stable order.
    """
    seen = {}
    for v in variables:
        key = str(v).strip().lower()
        if not key:
            continue
        if key not in CORRDIFF_CHANNEL:
            raise ValueError(
                f"Unknown variable '{v}'. Choose from: {', '.join(CORRDIFF_VARIABLES)}."
            )
        seen[key] = True
    if not seen:
        raise ValueError("No variables given.")
    return sorted(seen, key=CORRDIFF_CHANNEL.get)


def _validate_percentiles(percentiles: Iterable[int]) -> list[int]:
    """Sort/validate percentiles; each must be in 0..100. Empty is allowed."""
    out = sorted({int(p) for p in percentiles})
    bad = [p for p in out if p < 0 or p > 100]
    if bad:
        raise ValueError(f"Percentiles must be between 0 and 100; got {bad}.")
    return out


def run_prediction(
    username: str | None,
    year: int,
    month: int,
    day: int,
    hours: Sequence[int],
    samples: int,
    steps: int,
    variables: Sequence[str] = DEFAULT_VARIABLES,
    percentiles: Sequence[int] = DEFAULT_PERCENTILES,
    output_root: str | Path = "corrdiff_predictions",
    seed: int = 0,
    ds_gefs=None,
    ds_gefs_select=None,
    host: str | None = None,
) -> Path:
    """Run the full CorrDiff prediction for one initial condition.

    ``host`` selects which NIM to hit -- see ``nim_base_url()``.

    For each forecast hour it builds the GEFS input, runs NIM inference, then,
    for each requested output variable, saves every sample's field plus these
    ensemble statistics (all elementwise across the samples) into
    ``<output_root>/<YYYY-MM-DD>/``:

        {YYYY-MM-DD}_{var}_H{H:03d}_sample{i:02d}.npy   # raw samples
        {YYYY-MM-DD}_{var}_H{H:03d}_ensemble_mean.npy
        {YYYY-MM-DD}_{var}_H{H:03d}_ensemble_std.npy
        {YYYY-MM-DD}_{var}_H{H:03d}_ensemble_max.npy
        {YYYY-MM-DD}_{var}_H{H:03d}_ensemble_min.npy
        {YYYY-MM-DD}_{var}_H{H:03d}_p{pp:02d}.npy        # one per percentile

    ``variables`` may name any of ``CORRDIFF_VARIABLES`` (u10m, v10m, t2m, tp,
    csnow, cicep, cfrzr, crain); more than one is allowed. ``percentiles`` are
    computed across samples (default 10/25/50/75/90).

    Data sources may be passed in (recommended for a back end that reuses them
    across requests); if omitted they are created here.

    Returns the output directory path.
    """
    hours = validate_hours(hours)
    variables = validate_variables(variables)
    percentiles = _validate_percentiles(percentiles)
    if ds_gefs is None or ds_gefs_select is None:
        ds_gefs, ds_gefs_select = setup_data_sources()

    ds = date_str(year, month, day)
    out_dir = Path(output_root) / ds
    out_dir.mkdir(parents=True, exist_ok=True)

    ic_time = datetime(year, month, day)

    for H in hours:
        print(
            f"\n=== {ds}  H{H:03d}  "
            f"(vars={','.join(variables)}, samples={samples}, steps={steps}) ==="
        )
        input_array = fetch_input_gefs(
            ds_gefs, ds_gefs_select, ic_time, timedelta(hours=H)
        )

        print("Sending inference request to NIM ...")
        raw_samples = infer_corrdiff(
            username, input_array, samples=samples, steps=steps, seed=seed,
            host=host,
        )
        print(f"Received {len(raw_samples)} sample(s).")

        # Extract and save each requested variable from its output channel.
        for var in variables:
            ch = CORRDIFF_CHANNEL[var]
            var_samples = []
            for i, sample_data in enumerate(raw_samples):
                field = sample_data[0, 0, ch]
                var_samples.append(field)
                sample_path = out_dir / f"{ds}_{var}_H{H:03d}_sample{i:02d}.npy"
                np.save(sample_path, field)
            print(f"  [{var}] saved {len(var_samples)} sample(s)")

            # Ensemble statistics, all elementwise across the samples.
            stack = np.stack(var_samples, axis=0)  # (n_samples, H, W)
            prefix = f"{ds}_{var}_H{H:03d}"

            np.save(out_dir / f"{prefix}_ensemble_mean.npy", stack.mean(axis=0))
            np.save(out_dir / f"{prefix}_ensemble_std.npy", stack.std(axis=0))
            np.save(out_dir / f"{prefix}_ensemble_max.npy", stack.max(axis=0))
            np.save(out_dir / f"{prefix}_ensemble_min.npy", stack.min(axis=0))
            print(f"  [{var}] saved ensemble mean/std/max/min")

            for pp in percentiles:
                pct_field = np.percentile(stack, pp, axis=0)
                np.save(out_dir / f"{prefix}_p{pp:02d}.npy", pct_field)
            if percentiles:
                print(f"  [{var}] saved percentiles: "
                      f"{', '.join(f'p{p:02d}' for p in percentiles)}")

    print(f"\nDone. Predictions saved in: {out_dir}")
    return out_dir


# --------------------------------------------------------------------------- #
# Interactive CLI
# --------------------------------------------------------------------------- #

def _prompt_int(msg: str, minimum: int | None = None, maximum: int | None = None) -> int:
    while True:
        try:
            val = int(input(msg).strip())
        except ValueError:
            print("  Please enter a whole number.")
            continue
        if minimum is not None and val < minimum:
            print(f"  Must be >= {minimum}.")
            continue
        if maximum is not None and val > maximum:
            print(f"  Must be <= {maximum}.")
            continue
        return val


def _prompt_hours(msg: str) -> list[int]:
    while True:
        raw = input(msg).strip()
        try:
            hours = validate_hours(int(h) for h in raw.replace(",", " ").split())
        except ValueError as exc:
            print(f"  {exc}")
            continue
        return hours


def _prompt_variables(msg: str) -> list[str]:
    while True:
        raw = input(msg).strip()
        try:
            return validate_variables(raw.replace(",", " ").split())
        except ValueError as exc:
            print(f"  {exc}")
            continue


def main(argv: Sequence[str] | None = None) -> int:
    """Interactive driver reproducing the notebook flow."""
    parser = argparse.ArgumentParser(description="Run CorrDiff predictions via the NIM.")
    parser.add_argument(
        "--username",
        help="Public Ingress owner's username -- reaches "
             "https://corrdiff-<username>.nrp-nautilus.io. No built-in auth "
             "on that endpoint; only use one you trust.",
    )
    parser.add_argument(
        "--nim-host",
        help="Explicit NIM host/URL, overriding --username entirely (or set "
             "CORRDIFF_NIM_HOST). Accepts host:port or a full http(s)://... URL. "
             "Most users won't need this.",
    )
    parser.add_argument("--year", type=int)
    parser.add_argument("--month", type=int)
    parser.add_argument("--day", type=int)
    parser.add_argument(
        "--hours",
        help=f"Comma-separated forecast hours, each 1..{MAX_FORECAST_HOUR} "
             "(e.g. 3,6,9,12,15,18,21,24).",
    )
    parser.add_argument(
        "--variables",
        help="Comma-separated output variables to save. Choose from: "
             f"{', '.join(CORRDIFF_VARIABLES)} (e.g. tp,t2m). More than one allowed.",
    )
    parser.add_argument("--samples", type=int, help="Number of ensemble samples.")
    parser.add_argument("--steps", type=int, help="Number of diffusion steps.")
    parser.add_argument(
        "--percentiles",
        help="Comma-separated ensemble percentiles to save, each 0..100 "
             f"(default {','.join(map(str, DEFAULT_PERCENTILES))}). Use 'none' to skip.",
    )
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument(
        "--output-root", default="corrdiff_predictions",
        help="Root folder for saved predictions (a <YYYY-MM-DD> subfolder is created).",
    )
    parser.add_argument(
        "--skip-health-check", action="store_true",
        help="Run even if the NIM health check fails.",
    )
    args = parser.parse_args(argv)

    # ---- 1. Username/host + NIM health check ----------------------------- #
    nim_host = args.nim_host or os.environ.get("CORRDIFF_NIM_HOST")
    username = args.username
    if not nim_host and not username:
        username = input("NIM username (its public Ingress owner): ").strip()
    print(f"\nChecking NIM health ({nim_base_url(username, nim_host)}) ...")
    healthy = check_nim_health(username, host=nim_host)
    if not healthy and not args.skip_health_check:
        cont = input("NIM is not ready. Continue anyway? [y/N]: ").strip().lower()
        if cont not in ("y", "yes"):
            print("Aborting.")
            return 1

    # ---- 2. Set up the model / data sources ----------------------------- #
    print("\nSetting up GEFS data sources ...")
    ds_gefs, ds_gefs_select = setup_data_sources()

    # ---- 3. Initial condition + forecast configuration ------------------ #
    year = args.year if args.year is not None else _prompt_int("Initial condition year (e.g. 2025): ", 1900, 2100)
    month = args.month if args.month is not None else _prompt_int("Initial condition month (1-12): ", 1, 12)
    day = args.day if args.day is not None else _prompt_int("Initial condition day (1-31): ", 1, 31)

    if args.hours:
        hours = validate_hours(int(h) for h in args.hours.replace(",", " ").split())
    else:
        hours = _prompt_hours(
            f"Forecast hours to predict (comma-separated, up to {MAX_FORECAST_HOUR}): "
        )

    if args.variables:
        variables = validate_variables(args.variables.replace(",", " ").split())
    else:
        variables = _prompt_variables(
            "Variables to predict (comma-separated; "
            f"options: {', '.join(CORRDIFF_VARIABLES)}): "
        )

    samples = args.samples if args.samples is not None else _prompt_int("Number of samples: ", 1)
    steps = args.steps if args.steps is not None else _prompt_int("Number of steps: ", 1)

    if args.percentiles is None:
        percentiles = DEFAULT_PERCENTILES
    elif args.percentiles.strip().lower() in ("none", ""):
        percentiles = []
    else:
        percentiles = _validate_percentiles(
            int(p) for p in args.percentiles.replace(",", " ").split()
        )

    # ---- 4. Run --------------------------------------------------------- #
    run_prediction(
        username=username,
        year=year,
        month=month,
        day=day,
        hours=hours,
        samples=samples,
        steps=steps,
        variables=variables,
        percentiles=percentiles,
        output_root=args.output_root,
        seed=args.seed,
        ds_gefs=ds_gefs,
        ds_gefs_select=ds_gefs_select,
        host=nim_host,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
