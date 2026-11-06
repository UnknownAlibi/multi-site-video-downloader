"""生成 NSIS MUI2 使用的安装向导图片（BMP）。"""

from pathlib import Path

from PIL import Image

BASE = Path(__file__).resolve().parent
ASSETS = BASE / "assets"


def fit(image, size):
    copy = image.copy()
    copy.thumbnail(size, Image.LANCZOS)
    return copy


def vertical_gradient(size, top, bottom):
    width, height = size
    image = Image.new("RGB", size, top)
    pixels = image.load()
    for y in range(height):
        ratio = y / max(height - 1, 1)
        color = tuple(
            int(top[index] + (bottom[index] - top[index]) * ratio)
            for index in range(3)
        )
        for x in range(width):
            pixels[x, y] = color
    return image


def main():
    logo_path = ASSETS / "logo.png"
    mascot_path = ASSETS / "mascot_left.png"

    header = vertical_gradient((150, 57), (250, 253, 255), (230, 242, 255))
    if logo_path.exists():
        logo = Image.open(logo_path).convert("RGBA")
        logo = fit(logo, (42, 42))
        header = header.convert("RGBA")
        header.alpha_composite(
            logo,
            (150 - logo.width - 10, (57 - logo.height) // 2),
        )
        header = header.convert("RGB")
    header.save(BASE / "installer_header.bmp")

    welcome = vertical_gradient((164, 314), (255, 255, 255), (226, 240, 255)).convert(
        "RGBA"
    )
    if mascot_path.exists():
        mascot = Image.open(mascot_path).convert("RGBA")
        mascot = fit(mascot, (152, 292))
        welcome.alpha_composite(
            mascot,
            ((164 - mascot.width) // 2, 314 - mascot.height - 4),
        )
    if logo_path.exists():
        logo = Image.open(logo_path).convert("RGBA")
        logo = fit(logo, (44, 44))
        welcome.alpha_composite(logo, (10, 10))
    welcome.convert("RGB").save(BASE / "installer_welcome.bmp")


if __name__ == "__main__":
    main()
