#!/usr/bin/env python3
"""Kurage SEO のOGP画像(1200x630)を作る。

kappstore/scripts/make_ogp.py と同じ寸法・フォントで作り、Kurageシリーズの
カードとして並んだときに揃って見えるようにする。配色だけ kseo の藍にする
(kgeo の緑と並べたときに別製品だと分かるように)。

実行: python3 scripts/make_ogp.py  →  landing/assets/ogp.png
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
FONT_B = "/usr/share/fonts/opentype/noto/NotoSansCJK-Black.ttc"
FONT_M = "/usr/share/fonts/opentype/noto/NotoSansCJK-Medium.ttc"
FONT_R = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"

W, H = 1200, 630
FOAM = "#f5f8fb"
PANEL = "#e9f0f6"
LINE = "#d3e2ee"
INK = "#14293c"
MUTED = "#5a6f80"
ACCENT = "#1d6fa5"
ACCENT_DEEP = "#14567f"


def font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def text_width(draw: ImageDraw.ImageDraw, s: str, f) -> int:
    return draw.textbbox((0, 0), s, font=f)[2]


def main() -> None:
    img = Image.new("RGB", (W, H), FOAM)
    d = ImageDraw.Draw(img)

    # 上端のアクセント帯。シリーズで揃える意匠。
    d.rectangle([0, 0, W, 10], fill=ACCENT)

    # 右側にマスコット。まず置いてから、左のテキスト幅を決める。
    avatar_path = ROOT / "landing" / "assets" / "kurage_avatar.png"
    avatar_right_edge = W - 70
    if avatar_path.exists():
        av = Image.open(avatar_path).convert("RGBA")
        size = 330
        av = av.resize((size, size), Image.LANCZOS)
        # 角丸マスクに画像自身のアルファを掛け合わせる。角丸だけを使うと
        # 透過部分が黒く塗り潰される(1回やった)。
        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, size, size], radius=44, fill=255)
        from PIL import ImageChops

        mask = ImageChops.multiply(mask, av.split()[3])
        box = (avatar_right_edge - size, 168)
        d.rounded_rectangle(
            [box[0] - 14, box[1] - 14, box[0] + size + 14, box[1] + size + 14],
            radius=58, fill=PANEL, outline=LINE, width=3,
        )
        img.paste(av, box, mask)

    left = 72
    # 製品名
    d.text((left, 92), "Kurage SEO", font=font(FONT_B, 62), fill=INK)

    # 見出し。2行に割って、どちらも同じ左端から始める。
    f_h = font(FONT_B, 46)
    d.text((left, 196), "日本語サイトのSEOを、", font=f_h, fill=INK)
    d.text((left, 258), "実測で診断する。", font=f_h, fill=ACCENT_DEEP)

    # 差別化の芯を数字で1行。
    f_s = font(FONT_M, 27)
    d.text((left, 346), "タイトルは全角で数える。文字コードもlangも見る。", font=f_s, fill=MUTED)

    # 根拠を3つ、小さなタグで。
    f_t = font(FONT_M, 23)
    x = left
    for label in ("判定はLLM不使用", "10カテゴリ", "月3回無料"):
        w = text_width(d, label, f_t)
        d.rounded_rectangle([x, 404, x + w + 34, 452], radius=24, fill=PANEL, outline=LINE, width=2)
        d.text((x + 17, 412), label, font=f_t, fill=ACCENT_DEEP)
        x += w + 34 + 14

    # 下端: ドメインと社名
    d.rectangle([0, H - 78, W, H], fill=PANEL)
    d.line([0, H - 78, W, H - 78], fill=LINE, width=2)
    f_f = font(FONT_M, 25)
    d.text((left, H - 56), "kseo.exbridge.jp", font=f_f, fill=ACCENT_DEEP)
    f_c = font(FONT_R, 23)
    corp = "株式会社エクスブリッジ"
    d.text((W - 72 - text_width(d, corp, f_c), H - 55), corp, font=f_c, fill=MUTED)

    out = ROOT / "landing" / "assets" / "ogp.png"
    img.save(out, optimize=True)
    print(f"wrote {out} ({out.stat().st_size:,} bytes, {W}x{H})")


if __name__ == "__main__":
    main()
