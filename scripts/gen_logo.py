#!/usr/bin/env python
"""Generate a large gold 'EUC Planet'-style planet logo -> web/static/logo.png."""
import os

from PIL import Image, ImageDraw, ImageFilter

N = 1024
cx = cy = N // 2
R = 330
C0, C1, C2 = (255, 247, 214), (247, 198, 66), (176, 116, 16)


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


img = Image.new("RGBA", (N, N), (0, 0, 0, 0))

# soft outer glow
glow = Image.new("RGBA", (N, N), (0, 0, 0, 0))
ImageDraw.Draw(glow).ellipse([cx - R - 90, cy - R - 90, cx + R + 90, cy + R + 90], fill=(255, 196, 60, 60))
img = Image.alpha_composite(img, glow.filter(ImageFilter.GaussianBlur(55)))

# planet: radial gold gradient via concentric circles
planet = Image.new("RGBA", (N, N), (0, 0, 0, 0))
pd = ImageDraw.Draw(planet)
for r in range(R, 0, -1):
    t = r / R
    col = lerp(C0, C1, t / 0.6) if t < 0.6 else lerp(C1, C2, (t - 0.6) / 0.4)
    pd.ellipse([cx - r, cy - r, cx + r, cy + r], fill=col + (255,))
img = Image.alpha_composite(img, planet)

# tilted orbit ring
orb = Image.new("RGBA", (N, N), (0, 0, 0, 0))
ImageDraw.Draw(orb).ellipse([cx - 470, cy - 150, cx + 470, cy + 150], outline=(255, 214, 90, 255), width=16)
img = Image.alpha_composite(img, orb.rotate(-22, resample=Image.BICUBIC, center=(cx, cy)))

# satellite on the orbit
sx, sy = cx + 436, cy - 176
ImageDraw.Draw(img).ellipse([sx - 24, sy - 24, sx + 24, sy + 24], fill=(255, 224, 120, 255))

out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web", "static", "logo.png")
img.save(out)
print("wrote", out, img.size)
