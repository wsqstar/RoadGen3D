from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


root = Path(__file__).resolve().parents[1]
src = root / "qa" / "rendered-v3"
dst = root / "qa" / "contact_sheets-v3"
dst.mkdir(parents=True, exist_ok=True)
pages = sorted(src.glob("page-*.png"), key=lambda p: int(p.stem.split("-")[1]))
font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Unicode.ttf", 28)

for sheet_idx in range(0, len(pages), 4):
    batch = pages[sheet_idx:sheet_idx + 4]
    thumbs = []
    for path in batch:
        im = Image.open(path).convert("RGB")
        im.thumbnail((700, 900))
        thumbs.append((path, im.copy()))
    canvas = Image.new("RGB", (1500, 1950), "#d9dde0")
    draw = ImageDraw.Draw(canvas)
    for idx, (path, im) in enumerate(thumbs):
        col, row = idx % 2, idx // 2
        x, y = 30 + col * 740, 55 + row * 950
        canvas.paste(im, (x, y))
        draw.text((x, y - 38), path.stem, fill="black", font=font)
    out = dst / f"sheet-{sheet_idx // 4 + 1}.png"
    canvas.save(out)
    print(out)
