# FILE: tools/build_site.py
"""
Google Drive (public folder) -> GitHub Pages builder.

Usage:
  python tools/build_site.py --sync-drive
  python tools/build_site.py --build

Input (synced):
  content/drive/**.(docx|pdf)

Output (generated):
  docs/index.md
  docs/notes/**.html
  docs/downloads/**.(docx|pdf)
  docs/assets/** (images extracted from docx)

Notes:
  - GitHub Pages does not convert DOCX to HTML automatically.
  - Drive folder must be shared as "Anyone with the link" (public).
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional

import gdown
import mammoth
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "content" / "drive"
DOCS_DIR = ROOT / "docs"

OUT_NOTES = DOCS_DIR / "notes"
OUT_DOWNLOADS = DOCS_DIR / "downloads"
OUT_ASSETS = DOCS_DIR / "assets"

GDRIVE_URL = os.environ.get("GDRIVE_FOLDER_URL", "").strip()


@dataclass(frozen=True)
class Entry:
    kind: str  # "docx" | "pdf"
    title: str
    rel_dir: Path
    rel_stem: str
    src: Path
    out_html: Optional[Path]
    out_file: Path


def main() -> None:
    args = parse_args()

    if args.sync_drive:
        if not GDRIVE_URL:
            raise SystemExit("GDRIVE_FOLDER_URL env is empty.")
        sync_drive_folder(GDRIVE_URL, SRC_DIR)

    if args.build:
        ensure_dirs()
        clean_generated_dirs()
        entries = collect_entries(SRC_DIR)
        for e in entries:
            if e.kind == "docx":
                build_docx(e)
            elif e.kind == "pdf":
                build_pdf(e)
        build_index(entries)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--sync-drive", action="store_true")
    p.add_argument("--build", action="store_true")
    return p.parse_args()


def ensure_dirs() -> None:
    SRC_DIR.mkdir(parents=True, exist_ok=True)
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    (DOCS_DIR / ".nojekyll").write_text("", encoding="utf-8")


def clean_generated_dirs() -> None:
    for d in (OUT_NOTES, OUT_DOWNLOADS, OUT_ASSETS):
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True, exist_ok=True)


def sync_drive_folder(url: str, out_dir: Path) -> None:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    # Public folder required. If not public, download will fail.
    gdown.download_folder(url=url, output=str(out_dir), quiet=False, use_cookies=False)


def slugify(text: str) -> str:
    t = text.strip().lower()
    t = re.sub(r"[^a-z0-9]+", "-", t)
    t = re.sub(r"-{2,}", "-", t).strip("-")
    return t or "item"


def safe_rel_dir(rel_dir: Path) -> Path:
    if str(rel_dir) == ".":
        return Path()
    return Path(*[slugify(p) for p in rel_dir.parts if p and p != "."])


def title_from_path(p: Path) -> str:
    return p.stem.replace("_", " ").replace("-", " ").strip() or p.stem


def collect_entries(root: Path) -> List[Entry]:
    files = sorted([p for p in root.rglob("*") if p.is_file()], key=lambda x: str(x).lower())
    entries: List[Entry] = []

    for f in files:
        ext = f.suffix.lower()
        if ext not in {".docx", ".pdf"}:
            continue

        rel = f.relative_to(root)
        rel_dir = safe_rel_dir(rel.parent)
        rel_stem = slugify(rel.stem)
        title = title_from_path(f)

        out_dir_notes = OUT_NOTES / rel_dir
        out_dir_dl = OUT_DOWNLOADS / rel_dir
        out_dir_notes.mkdir(parents=True, exist_ok=True)
        out_dir_dl.mkdir(parents=True, exist_ok=True)

        if ext == ".docx":
            out_html = out_dir_notes / f"{rel_stem}.html"
            out_file = out_dir_dl / f"{rel_stem}.docx"
            entries.append(
                Entry(
                    kind="docx",
                    title=title,
                    rel_dir=rel_dir,
                    rel_stem=rel_stem,
                    src=f,
                    out_html=out_html,
                    out_file=out_file,
                )
            )
        else:
            out_html = out_dir_notes / f"{rel_stem}.pdf.html"
            out_file = out_dir_dl / f"{rel_stem}.pdf"
            entries.append(
                Entry(
                    kind="pdf",
                    title=title,
                    rel_dir=rel_dir,
                    rel_stem=rel_stem,
                    src=f,
                    out_html=out_html,
                    out_file=out_file,
                )
            )

    return entries


def rel_from(frm: Path, to: Path) -> Path:
    return Path(os.path.relpath(to, start=frm.parent))


def content_type_to_ext(ct: str) -> str:
    m = {
        "image/png": "png",
        "image/jpeg": "jpg",
        "image/jpg": "jpg",
        "image/gif": "gif",
        "image/svg+xml": "svg",
        "image/webp": "webp",
    }
    return m.get(ct, "bin")


def wrap_html(*, title: str, body_html: str, download_href: str, home_href: str) -> str:
    soup = BeautifulSoup("", "html.parser")

    html = soup.new_tag("html", lang="tr")
    head = soup.new_tag("head")
    head.append(soup.new_tag("meta", charset="utf-8"))
    head.append(soup.new_tag("meta", attrs={"name": "viewport", "content": "width=device-width, initial-scale=1"}))

    t = soup.new_tag("title")
    t.string = title
    head.append(t)

    style = soup.new_tag("style")
    style.string = """
      body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;line-height:1.6;margin:0}
      .wrap{max-width:980px;margin:0 auto;padding:24px}
      .topbar{position:sticky;top:0;background:#fff;border-bottom:1px solid #eee}
      .topbar .wrap{display:flex;gap:12px;align-items:center;justify-content:space-between}
      .btns{display:flex;gap:10px;flex-wrap:wrap}
      a.btn{display:inline-block;padding:10px 12px;border:1px solid #ddd;border-radius:12px;text-decoration:none;color:inherit}
      a.btn:hover{background:#fafafa}
      img{max-width:100%;height:auto}
      table{border-collapse:collapse;width:100%;overflow-x:auto;display:block}
      th,td{border:1px solid #ddd;padding:8px}
      pre,code{background:#f6f8fa;border-radius:8px}
      pre{padding:12px;overflow:auto}
    """
    head.append(style)

    body = soup.new_tag("body")

    topbar = soup.new_tag("div", attrs={"class": "topbar"})
    topwrap = soup.new_tag("div", attrs={"class": "wrap"})
    title_div = soup.new_tag("div")
    title_div.string = title

    btns = soup.new_tag("div", attrs={"class": "btns"})
    a_home = soup.new_tag("a", href=home_href, attrs={"class": "btn"})
    a_home.string = "← Ana sayfa"
    a_dl = soup.new_tag("a", href=download_href, attrs={"class": "btn"})
    a_dl.string = "⬇️ İndir"
    btns.extend([a_home, a_dl])

    topwrap.extend([title_div, btns])
    topbar.append(topwrap)

    content = soup.new_tag("div", attrs={"class": "wrap"})
    content.append(BeautifulSoup(body_html, "html.parser"))

    body.extend([topbar, content])
    html.extend([head, body])
    soup.append(html)
    return "<!doctype html>\n" + str(soup)


def build_docx(e: Entry) -> None:
    assert e.out_html is not None
    shutil.copy2(e.src, e.out_file)

    img_dir = OUT_ASSETS / e.rel_dir / e.rel_stem
    img_dir.mkdir(parents=True, exist_ok=True)
    img_counter = {"i": 0}

    def convert_image(image: mammoth.images.Image) -> dict:
        img_counter["i"] += 1
        ext = content_type_to_ext(image.content_type)
        filename = f"img-{img_counter['i']:03d}.{ext}"
        out_path = img_dir / filename
        out_path.write_bytes(image.read())

        img_rel = rel_from(e.out_html, out_path)
        return {"src": img_rel.as_posix()}

    with e.src.open("rb") as f:
        result = mammoth.convert_to_html(f, convert_image=mammoth.images.img_element(convert_image))

    full = wrap_html(
        title=e.title,
        body_html=result.value,
        download_href=rel_from(e.out_html, e.out_file).as_posix(),
        home_href=rel_from(e.out_html, DOCS_DIR / "index.md").as_posix(),
    )
    e.out_html.write_text(full, encoding="utf-8")


def build_pdf(e: Entry) -> None:
    assert e.out_html is not None
    shutil.copy2(e.src, e.out_file)

    pdf_href = rel_from(e.out_html, e.out_file).as_posix()
    body_html = f"""
      <p><a class="btn" href="{pdf_href}">⬇️ PDF indir</a></p>
      <iframe src="{pdf_href}" width="100%" height="900" style="border:1px solid #ddd; border-radius:12px;"></iframe>
    """

    full = wrap_html(
        title=e.title,
        body_html=body_html,
        download_href=pdf_href,
        home_href=rel_from(e.out_html, DOCS_DIR / "index.md").as_posix(),
    )
    e.out_html.write_text(full, encoding="utf-8")


def build_index(entries: Iterable[Entry]) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    entries = list(entries)

    lines = [
        "# FSP Notları",
        "",
        f"_Otomatik güncellendi: {now}_",
        "",
        "## İçerik",
        "",
    ]

    if not entries:
        lines += ["Henüz Drive’dan (DOCX/PDF) içerik indirilemedi.", ""]
        (DOCS_DIR / "index.md").write_text("\n".join(lines), encoding="utf-8")
        return

    grouped: dict[str, List[Entry]] = {}
    for e in entries:
        key = str(e.rel_dir) if str(e.rel_dir) else "Kök"
        grouped.setdefault(key, []).append(e)

    for group_name in sorted(grouped.keys(), key=lambda x: x.lower()):
        lines.append(f"### {group_name}")
        lines.append("")
        for e in sorted(grouped[group_name], key=lambda x: x.title.lower()):
            assert e.out_html is not None
            html_href = Path("notes") / e.rel_dir / e.out_html.name
            dl_href = Path("downloads") / e.rel_dir / e.out_file.name
            icon = "📖" if e.kind == "docx" else "📄"
            lines.append(f"- {icon} [{e.title}]({html_href.as_posix()}) · ⬇️ [{e.kind.upper()}]({dl_href.as_posix()})")
        lines.append("")

    (DOCS_DIR / "index.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
