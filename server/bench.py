#!/usr/bin/env python3
"""POST each page in a directory to the adapter and write timings."""

import argparse
import csv
import os
import re
import sys
import time
from pathlib import Path

import httpx

MIME = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}
_NUMBERS = re.compile(r"(\d+)")


def natural_key(path: Path):
    parts = _NUMBERS.split(path.name)
    return [int(part) if part.isdigit() else part.lower() for part in parts]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Translate each image in a directory via the adapter"
    )
    parser.add_argument("image_dir", type=Path)
    parser.add_argument(
        "service_url",
        help="adapter base URL, for example http://127.0.0.1:8100",
    )
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args(argv)

    token = os.environ.get("MANGAFLOW_TOKEN", "").strip()
    if not token:
        print("MANGAFLOW_TOKEN is not set", file=sys.stderr)
        return 2
    if not args.image_dir.is_dir():
        print(f"not a directory: {args.image_dir}", file=sys.stderr)
        return 2

    images = sorted(
        (
            path
            for path in args.image_dir.iterdir()
            if path.is_file() and path.suffix.lower() in MIME
        ),
        key=natural_key,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    base = args.service_url.rstrip("/")
    url = base if base.endswith("/translate") else base + "/translate"
    failures = 0

    with args.output_dir.joinpath("results.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        writer = csv.writer(handle)
        writer.writerow(["filename", "status", "seconds", "output_bytes"])
        with httpx.Client(timeout=320.0) as client:
            for path in images:
                started = time.perf_counter()
                status = 0
                payload = b""
                try:
                    response = client.post(
                        url,
                        content=path.read_bytes(),
                        headers={
                            "Authorization": f"Bearer {token}",
                            "Content-Type": MIME[path.suffix.lower()],
                        },
                    )
                    status = response.status_code
                    payload = response.content
                except httpx.HTTPError as exc:
                    print(f"{path.name} {type(exc).__name__}", file=sys.stderr)
                elapsed = time.perf_counter() - started
                if status == 200 and payload:
                    args.output_dir.joinpath(path.stem + ".png").write_bytes(payload)
                else:
                    failures += 1
                writer.writerow([path.name, status, f"{elapsed:.3f}", len(payload)])
                handle.flush()
                print(
                    f"{path.name} {status} {elapsed:.3f}s {len(payload)}",
                    file=sys.stderr,
                )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
