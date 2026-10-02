"""Generate platform icons from the same geometric AppLens mark (Pillow)."""
from pathlib import Path
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'assets' / 'applens'
OUT.mkdir(parents=True, exist_ok=True)
# A lens encloses three observed data columns. Its handle is the forward action.
im = Image.new('RGBA', (1024, 1024))
d = ImageDraw.Draw(im)
d.rounded_rectangle((0, 0, 1023, 1023), radius=224, fill='#2463EB')
d.line((632, 632, 780, 780), fill='white', width=88)
d.ellipse((736, 736, 824, 824), fill='white')
d.ellipse((210, 194, 730, 714), outline='white', width=68)
for box, color in [((336, 436, 392, 548), '#A5F3FC'),
                   ((444, 348, 500, 548), 'white'),
                   ((552, 392, 608, 548), '#A5F3FC')]:
    d.rounded_rectangle(box, radius=28, fill=color)
im.save(OUT / 'applens.png')
im.save(OUT / 'applens.ico', sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
im.save(OUT / 'applens.icns')
print(OUT)
