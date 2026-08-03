"""
Smoke test for the publicly hosted CorrDiff endpoint.

Only needs `requests` + `numpy` (no earth2studio, no torch, no GEFS download) so it's
fast to run on a machine that has never touched this repo before. It sends a
synthetic (meteorologically meaningless) input array just to confirm the plumbing
works end-to-end: DNS resolves, TLS works, the ingress accepts the upload, the NIM
runs, and a tar of .npy files comes back.

Usage:
    uv sync
    uv run python test_hosted_endpoint.py https://corrdiff-<host>.nrp-nautilus.io
"""

import io
import sys
import tarfile

import numpy as np
import requests

# Fixed by the model: 7 select vars + 30 pressure vars + 1 lead-time field = 38 channels,
# cropped to the 129x301 CONUS grid, with a leading batch dim. See HostedInference.md for
# what each channel means.
INPUT_SHAPE = (1, 1, 38, 129, 301)


def main():
    if len(sys.argv) != 2:
        print(f"Usage: python {sys.argv[0]} <base_url>")
        print("Example: python test_hosted_endpoint.py https://corrdiff-laurahu.nrp-nautilus.io")
        sys.exit(1)

    base_url = sys.argv[1].rstrip("/")

    print(f"1. Checking {base_url}/v1/health/ready ...")
    r = requests.get(f"{base_url}/v1/health/ready", timeout=30)
    if r.status_code != 200:
        print(f"   FAILED: status {r.status_code}, body: {r.text}")
        sys.exit(1)
    print(f"   OK: {r.text}")

    print("2. Building synthetic input array (not real weather data, just testing the pipe) ...")
    input_array = np.zeros(INPUT_SHAPE, dtype=np.float32)
    np.save("smoke_test_input.npy", input_array)

    print(f"3. Sending minimal inference request to {base_url}/v1/infer (samples=1, steps=2) ...")
    r = requests.post(
        f"{base_url}/v1/infer",
        headers={"accept": "application/x-tar"},
        data={"samples": 1, "steps": 2, "seed": 0},
        files={"input_array": ("input_array", open("smoke_test_input.npy", "rb"))},
        timeout=600,
    )
    if r.status_code != 200:
        print(f"   FAILED: status {r.status_code}, body: {r.content[:500]}")
        sys.exit(1)
    print(f"   OK: got {len(r.content)} bytes back")

    print("4. Unpacking response tar ...")
    with tarfile.open(fileobj=io.BytesIO(r.content)) as tar:
        members = tar.getmembers()
        print(f"   {len(members)} member(s) in archive")
        for m in members:
            arr = np.load(io.BytesIO(tar.extractfile(m).read()))
            print(f"   - {m.name}: shape={arr.shape}, dtype={arr.dtype}")

    print("\nSUCCESS: the hosted endpoint is reachable and returning valid inference output.")


if __name__ == "__main__":
    main()
