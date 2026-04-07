#!/usr/bin/env python3
"""Create a vertical spritesheet from PNG files matched by a glob pattern.

Example:
    python make_vertical_spritesheet.py "image_*.png" -o spritesheet.png
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import sys
from typing import List

from PIL import Image


def natural_key(path: str):
    """Sort paths like image_2.png before image_10.png."""
    name = os.path.basename(path)
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", name)]


def collect_images(pattern: str) -> List[str]:
    matches = glob.glob(pattern)
    pngs = [p for p in matches if p.lower().endswith(".png")]
    pngs.sort(key=natural_key)
    return pngs


def build_vertical_spritesheet(image_paths: List[str], output_path: str) -> None:
    images = [Image.open(path).convert("RGBA") for path in image_paths]
    try:
        max_width = max(img.width for img in images)
        total_height = sum(img.height for img in images)

        sheet = Image.new("RGBA", (max_width, total_height), (0, 0, 0, 0))

        y = 0
        for img in images:
            sheet.paste(img, (0, y))
            y += img.height

        sheet.save(output_path)
    finally:
        for img in images:
            img.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Stack PNG files into one vertical spritesheet in sorted order."
    )
    parser.add_argument(
        "pattern",
        help="Glob pattern for input images, e.g. 'image_*.png'",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="spritesheet.png",
        help="Output spritesheet path (default: spritesheet.png)",
    )
    args = parser.parse_args()

    image_paths = collect_images(args.pattern)
    if not image_paths:
        print(f"No PNG files matched pattern: {args.pattern}", file=sys.stderr)
        return 1

    build_vertical_spritesheet(image_paths, args.output)
    print(f"Created '{args.output}' with {len(image_paths)} images.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
