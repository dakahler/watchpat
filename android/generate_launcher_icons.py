"""Generate Android launcher icon rasters from the shared app icon."""

from pathlib import Path

from PIL import Image


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ICON = REPO_ROOT / "assets" / "icons" / "watchpat_app_icon_1024.png"
RES_ROOT = REPO_ROOT / "android" / "app" / "src" / "main" / "res"

SIZES = {
    "mipmap-mdpi": 48,
    "mipmap-hdpi": 72,
    "mipmap-xhdpi": 96,
    "mipmap-xxhdpi": 144,
    "mipmap-xxxhdpi": 192,
}


def main() -> None:
    icon = Image.open(SOURCE_ICON).convert("RGBA")
    for folder, size in SIZES.items():
        out_dir = RES_ROOT / folder
        out_dir.mkdir(parents=True, exist_ok=True)
        resized = icon.resize((size, size), Image.LANCZOS)
        resized.save(out_dir / "ic_launcher.png")
        resized.save(out_dir / "ic_launcher_round.png")


if __name__ == "__main__":
    main()
