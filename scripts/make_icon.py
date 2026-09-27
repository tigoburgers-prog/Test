"""Generate the application icon (PNG + multi-size ICO) with Pillow."""

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]


def draw(size: int = 256) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size / 256
    d.rounded_rectangle((8 * s, 8 * s, 248 * s, 248 * s), radius=48 * s, fill=(31, 78, 121, 255))
    # road with lane markings
    d.polygon([(70 * s, 232 * s), (186 * s, 232 * s), (150 * s, 40 * s), (106 * s, 40 * s)], fill=(47, 58, 70, 255))
    for y in range(52, 232, 36):
        d.rectangle((125 * s, y * s, 131 * s, (y + 18) * s), fill=(235, 235, 235, 255))
    # warm and cold vehicles (relative movement)
    d.rounded_rectangle((92 * s, 150 * s, 118 * s, 196 * s), radius=6 * s, fill=(215, 48, 39, 255))
    d.rounded_rectangle((140 * s, 80 * s, 160 * s, 116 * s), radius=5 * s, fill=(69, 117, 180, 255))
    return img


if __name__ == "__main__":
    big = draw(256)
    big.save(ROOT / "traffic_snapshot" / "resources" / "app.png")
    big.save(ROOT / "packaging" / "app.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("ok")
