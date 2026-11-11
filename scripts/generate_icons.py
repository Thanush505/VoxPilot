"""Generate official VoxPilot application icons (.ico and .png) from the authoritative source image."""

from pathlib import Path
import shutil
import struct
from PIL import Image


SOURCE_IMAGE_PATH = Path(
    r"C:\Users\Thanu\.gemini\antigravity\brain\7c20afcd-13b0-42fa-baf3-c25fdc5fa176\.user_uploaded\media_1794405386981_7c1d4dda.jpg"
)
REPO_ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = REPO_ROOT / "assets" / "icons"
FRONTEND_PUBLIC_DIR = REPO_ROOT / "frontend" / "public"

ICO_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
PNG_SIZES = [16, 24, 32, 48, 64, 128, 180, 192, 256, 512, 1024]


def inspect_ico_entries(ico_path: Path) -> list[tuple[int, int, int]]:
    """Parse ICONDIR and ICONDIRENTRY headers to verify embedded resolutions."""
    data = ico_path.read_bytes()
    reserved, img_type, count = struct.unpack("<HHH", data[:6])
    assert reserved == 0 and img_type == 1, f"Invalid ICO header: {reserved}, {img_type}"
    entries = []
    offset = 6
    for _ in range(count):
        w, h, _pal, _res, _planes, bpp, size, _data_off = struct.unpack(
            "<BBBBHHII", data[offset : offset + 16]
        )
        width = 256 if w == 0 else w
        height = 256 if h == 0 else h
        entries.append((width, height, size))
        offset += 16
    return entries


def main() -> None:
    if not SOURCE_IMAGE_PATH.exists():
        raise FileNotFoundError(f"Authoritative source image not found: {SOURCE_IMAGE_PATH}")

    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    FRONTEND_PUBLIC_DIR.mkdir(parents=True, exist_ok=True)

    # Preserve exact uploaded source image in assets/icons/
    source_copy_path = ASSETS_DIR / "voxpilot-source.jpg"
    shutil.copy2(SOURCE_IMAGE_PATH, source_copy_path)

    with Image.open(SOURCE_IMAGE_PATH) as src_img:
        print(f"Loaded authoritative image: {SOURCE_IMAGE_PATH}")
        print(f"Original format={src_img.format}, mode={src_img.mode}, size={src_img.size}")

        # Convert to RGBA without modifying pixels, colors, or proportions
        rgba_master = src_img.convert("RGBA")

        # 1. Save full-resolution 1024x1024 master PNG
        master_png = ASSETS_DIR / "voxpilot-icon.png"
        rgba_master.save(master_png, format="PNG", optimize=True)
        print(f"Saved high-res master PNG: {master_png} ({master_png.stat().st_size} bytes)")

        # 2. Save individual LANCZOS-resampled PNGs for each required size
        for dim in PNG_SIZES:
            resized = (
                rgba_master
                if rgba_master.size == (dim, dim)
                else rgba_master.resize((dim, dim), Image.Resampling.LANCZOS)
            )
            out_png = ASSETS_DIR / f"voxpilot-icon-{dim}x{dim}.png"
            resized.save(out_png, format="PNG", optimize=True)
            print(f"Saved {dim}x{dim} PNG: {out_png.name} ({out_png.stat().st_size} bytes)")

        # 3. Generate multi-resolution Windows .ico (16, 24, 32, 48, 64, 128, 256)
        ico_path = ASSETS_DIR / "voxpilot.ico"
        rgba_master.save(ico_path, format="ICO", sizes=ICO_SIZES)
        entries = inspect_ico_entries(ico_path)
        print(f"Saved multi-resolution ICO: {ico_path} ({ico_path.stat().st_size} bytes)")
        print(f"Verified ICO embedded frames ({len(entries)}): {entries}")

    # 4. Copy required web & favicon assets to frontend/public/
    shutil.copy2(ASSETS_DIR / "voxpilot.ico", FRONTEND_PUBLIC_DIR / "favicon.ico")
    shutil.copy2(ASSETS_DIR / "voxpilot-icon-32x32.png", FRONTEND_PUBLIC_DIR / "favicon-32x32.png")
    shutil.copy2(ASSETS_DIR / "voxpilot-icon-16x16.png", FRONTEND_PUBLIC_DIR / "favicon-16x16.png")
    shutil.copy2(ASSETS_DIR / "voxpilot-icon-180x180.png", FRONTEND_PUBLIC_DIR / "apple-touch-icon.png")
    shutil.copy2(ASSETS_DIR / "voxpilot-icon-192x192.png", FRONTEND_PUBLIC_DIR / "voxpilot-logo.png")
    shutil.copy2(ASSETS_DIR / "voxpilot-icon-512x512.png", FRONTEND_PUBLIC_DIR / "voxpilot-icon-512.png")

    # Remove old default Vite placeholder favicon if present
    old_vite_svg = FRONTEND_PUBLIC_DIR / "vite.svg"
    if old_vite_svg.exists():
        old_vite_svg.unlink()
        print(f"Removed old default placeholder icon: {old_vite_svg}")

    print("All VoxPilot icon assets generated and verified successfully.")


if __name__ == "__main__":
    main()

