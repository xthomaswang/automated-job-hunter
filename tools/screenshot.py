#!/usr/bin/env python3
"""Render Markdown (as GitHub renders it), terminal text or an HTML fragment to a trimmed PNG.

For screenshots in pull request descriptions; see docs/COLLABORATION.md. macOS only: pages are
rendered by Quick Look's WebKit (qlmanage), so no browser is needed. Markdown goes through
GitHub's own renderer (`gh api markdown`), so tables and code look as they will on GitHub.

  uv run --with pillow tools/screenshot.py markdown IN.md OUT.png --title "Before: ..."
  uv run --with pillow tools/screenshot.py terminal IN.txt OUT.png --title "..."
  uv run --with pillow tools/screenshot.py html IN.html OUT.png --title "..."

In terminal text, lines starting with "- " are shown red, "+ " green and "# " grey.
"""

import argparse
import html
import json
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageChops

CSS = """
body{margin:0;padding:24px;width:880px;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;
font-size:15px;line-height:1.5;color:#1f2328;background:#ffffff}
.caption{font-size:13px;font-weight:600;color:#59636e;text-transform:uppercase;letter-spacing:.04em;margin:0 0 12px}
h1,h2,h3{border-bottom:1px solid #d1d9e0;padding-bottom:.3em;margin:20px 0 12px}h1{font-size:24px}h2{font-size:20px}h3{font-size:17px}
table{border-collapse:collapse;margin:12px 0;font-size:14px}th,td{border:1px solid #d1d9e0;padding:6px 12px;text-align:left}
th{background:#f6f8fa;font-weight:600}tr:nth-child(2n) td{background:#f6f8fa}
code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13px;background:#eff1f3;padding:.15em .35em;border-radius:6px}
pre{background:#f6f8fa;padding:12px 16px;border-radius:6px;overflow:hidden}pre code{background:none;padding:0}
a{color:#0969da;text-decoration:none}strong{font-weight:600}
.term{background:#0d1117;color:#e6edf3;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13px;line-height:1.55;
padding:16px 18px;border-radius:8px;white-space:pre-wrap;word-break:break-word}
.flow{display:flex;align-items:center;gap:10px;flex-wrap:nowrap;font-size:13px}.box{border:1px solid #d1d9e0;border-radius:8px;padding:10px 12px;background:#f6f8fa;text-align:center}.box b{display:block;font-size:13px;margin-bottom:4px}.arrow{color:#59636e;font-size:18px}.stack{display:flex;flex-direction:column;gap:6px}.ok{background:#dafbe1;border-color:#4ac26b}.warn{background:#fff8c5;border-color:#d4a72c}
.term .bad{color:#ff7b72}.term .good{color:#7ee787}.term .dim{color:#8b949e}
"""


def github_html(markdown: str) -> str:
    body = json.dumps({"text": markdown, "mode": "gfm", "context": "xthomaswang/automated-job-hunter"})
    done = subprocess.run(
        ["gh", "api", "markdown", "--input", "-"], input=body, capture_output=True, text=True, check=True
    )
    return done.stdout


def terminal_html(text: str) -> str:
    # Lines starting with "- " are marked as problems, "+ " as fixed, "# " as comments.
    out = []
    for line in text.splitlines():
        css = {"- ": "bad", "+ ": "good", "# ": "dim"}.get(line[:2])
        shown = html.escape(line[2:] if css else line)
        out.append(f'<span class="{css}">{shown}</span>' if css else shown)
    return '<div class="term">' + "\n".join(out) + "</div>"


def render(fragment: str, title: str, out: Path) -> None:
    page = f'<!doctype html><html><head><meta charset="utf-8"><style>{CSS}</style></head><body>'
    page += (f'<p class="caption">{html.escape(title)}</p>' if title else "") + fragment + "</body></html>"
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "page.html"
        source.write_text(page, encoding="utf-8")
        subprocess.run(["qlmanage", "-t", "-s", "1800", "-o", tmp, str(source)], capture_output=True, check=True)
        image = Image.open(Path(tmp) / "page.html.png").convert("RGB")
    corner = Image.new("RGB", image.size, image.getpixel((image.width - 1, image.height - 1)))
    box = ImageChops.difference(image, corner).getbbox()
    if box:
        pad = 16
        image = image.crop(
            (
                max(0, box[0] - pad),
                max(0, box[1] - pad),
                min(image.width, box[2] + pad),
                min(image.height, box[3] + pad),
            )
        )
    out.parent.mkdir(parents=True, exist_ok=True)
    image.save(out, optimize=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=["markdown", "terminal", "html"])
    parser.add_argument("source", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--title", default="")
    args = parser.parse_args()
    text = args.source.read_text(encoding="utf-8")
    fragment = {"markdown": github_html, "terminal": terminal_html, "html": lambda x: x}[args.kind](text)
    render(fragment, args.title, args.out)
    print(args.out)


if __name__ == "__main__":
    main()
