"""
Build a ZIP of images for testing the Bulk tab.

  python scripts/make_zip.py --src ~/Pictures --n 40 --out bulk_40.zip
  python scripts/make_zip.py --synthetic --n 30 --out bulk_30.zip

--src       folder with a few real photos; they get re-used with small random
            crops/flips/resizes until --n images exist
--synthetic no photos needed: draws random coloured shapes (captions will be
            silly, but it is fine for load testing)
"""
import argparse
import io
import random
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def synthetic(i: int) -> Image.Image:
    img = Image.new("RGB", (640, 480), tuple(random.randint(0, 255) for _ in range(3)))
    d = ImageDraw.Draw(img)
    for _ in range(random.randint(4, 10)):
        x, y = random.randint(0, 560), random.randint(0, 400)
        box = (x, y, x + random.randint(40, 200), y + random.randint(40, 200))
        col = tuple(random.randint(0, 255) for _ in range(3))
        (d.ellipse if random.random() < 0.5 else d.rectangle)(box, fill=col)
    return img


def variation(img: Image.Image) -> Image.Image:
    img = img.convert("RGB")
    w, h = img.size
    dx, dy = int(w * random.uniform(0, 0.15)), int(h * random.uniform(0, 0.15))
    img = img.crop((dx, dy, w - random.randint(0, dx + 1), h - random.randint(0, dy + 1)))
    if random.random() < 0.5:
        img = ImageOps.mirror(img)
    img.thumbnail((random.choice([512, 768, 1024]),) * 2)
    return img


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path)
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--out", type=Path, default=Path("bulk.zip"))
    args = ap.parse_args()

    sources = []
    if args.src:
        sources = [p for p in args.src.rglob("*") if p.suffix.lower() in EXTS]
    if not sources and not args.synthetic:
        ap.error("no images found in --src; pass --synthetic to generate some")

    with zipfile.ZipFile(args.out, "w", zipfile.ZIP_DEFLATED) as zf:
        for i in range(args.n):
            img = variation(Image.open(random.choice(sources))) if sources else synthetic(i)
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=88)
            zf.writestr(f"img_{i:03d}.jpg", buf.getvalue())
    print(f"wrote {args.out} with {args.n} images ({args.out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
