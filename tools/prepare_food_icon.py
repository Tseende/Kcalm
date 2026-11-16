"""Resize a generated transparent icon to the existing 112px WebP asset format.

python tools/prepare_food_icon.py source.png icon_name
Artwork generation happens separately; alpha is preserved, not keyed from a colour.
"""
from pathlib import Path
import argparse

from PIL import Image


def prepare(source: Path, target: Path) -> None:
    with Image.open(source) as raw:
        image = raw.convert('RGBA')
    alpha = image.getchannel('A')
    if alpha.getextrema()[0] == 255:
        raise ValueError('Expected a transparent image')
    bounds = alpha.point(lambda value: 255 if value > 24 else 0).getbbox()
    if bounds is None:
        raise ValueError('Empty image')
    image = image.crop(bounds)
    image.thumbnail((100, 100), Image.Resampling.LANCZOS)
    canvas = Image.new('RGBA', (112, 112), (0, 0, 0, 0))
    canvas.alpha_composite(image, ((112 - image.width) // 2, (112 - image.height) // 2))
    if target.exists():
        raise FileExistsError(target)
    canvas.save(target, 'WEBP', quality=88, method=6)
    print(f'{target.name}: {target.stat().st_size} bytes')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('name')
    args = parser.parse_args()
    if not args.name.isascii() or not args.name.isalnum():
        parser.error('name must be an ASCII alphanumeric icon key')
    prepare(args.source, Path(__file__).resolve().parents[1] / 'app/web/static/img/food' / f'{args.name}.webp')
