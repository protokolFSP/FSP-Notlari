# FILE: tools/build_site.py
"""
Google Drive (public folder) -> GitHub Pages static site builder (no Jekyll).

Features:
- Downloads a public Drive folder.
- Normalizes downloaded files by sniffing signatures:
  - %PDF -> .pdf
  - PK.. (zip) -> .docx (Drive export format=docx)
  - <html / <!doctype -> treated as permission/error page and fails with a clear message
- Generates:
  - docs/index.html
  - docs/notes/**/*.html
  - docs/downloads/** (original files)
  - docs/assets/** (extracted images from docx)
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

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
    out_html: Path
    out_file: Path
    sort_key: Tuple[int, str]


def main() -> None:
    args = parse_args()

    if args.sync_drive:
        require_env_url()
        sync_drive_folder(GDRIVE_URL, SRC_DIR)
        normalize_downloaded_files(SRC_DIR)
        assert_has_docs(SRC_DIR)

    if args.build:
        ensure_dirs()
        clean_generated_dirs()
        entries = collect_entries(SRC_DIR)
        for e in entries:
            if e.kind == "docx":
                build_docx(e)
            else:
                build_pdf(e)
        build_indexes(entries)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--sync-drive", action="store_true")
    p.add_argument("--build", action="store_true")
    return p.parse_args()


def require_env_url() -> None:
    if not GDRIVE_URL:
        raise SystemExit("GDRIVE_FOLDER_URL env is empty.")


def ensure_dirs() -> None:
    SRC_DIR.mkdir(parents=True, exist_ok=True)
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    (DOCS_DIR / ".nojekyll").write_text("", encoding="utf-8")


def clean_generated_dirs() -> None:
    for d in (OUT_NOTES, OUT_DOWNLOADS, OUT_ASSETS):
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True, exist_ok=True)


def extract_folder_id(url: str) -> Optional[str]:
    m = re.search(r"/folders/([a-zA-Z0-9_-]+)", url)
    if m:
        return m.group(1)
    m = re.search(r"[?&]id=([a-zA-Z0-9_-]+)", url)
    if m:
        return m.group(1)
    return None


def sync_drive_folder(url: str, out_dir: Path) -> None:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    folder_id = extract_folder_id(url)

    # 1) gdown API (try with cookies, then without)
    for use_cookies in (True, False):
        try:
            kwargs = {"output": str(out_dir), "quiet": False, "use_cookies": use_cookies}
            paths = (
                gdown.download_folder(id=folder_id, **kwargs)  # type: ignore[arg-type]
                if folder_id
                else gdown.download_folder(url=url, **kwargs)  # type: ignore[arg-type]
            )
            if paths:
                return
        except Exception as e:
            print(f"[sync] gdown API failed (use_cookies={use_cookies}): {e}")

    # 2) CLI fallback
    cmd = ["python", "-m", "gdown", "--folder", url, "-O", str(out_dir)]
    print(f"[sync] fallback CLI: {' '.join(cmd)}")
    subprocess.check_call(cmd)


def read_head(path: Path, n: int = 512) -> bytes:
    try:
        with path.open("rb") as f:
            return f.read(n)
    except OSError:
        return b""


def sniff_kind_and_error(path: Path) -> Tuple[Optional[str], Optional[str]]:
    head = read_head(path, 1024)
    if not head:
        return None, None

    if head.startswith(b"%PDF"):
        return "pdf", None

    if head.startswith(b"PK\x03\x04"):
        return "docx", None

    head_l = head.lstrip().lower()
    if head_l.startswith(b"<!doctype html") or head_l.startswith(b"<html"):
        snippet = head[:300].decode("utf-8", errors="ignore")
        return None, f"Downloaded HTML instead of a file for: {path.name}\n---\n{snippet}\n---"

    return None, None


def unique_path(target: Path) -> Path:
    if not target.exists():
        return target
    base = target.with_suffix("")
    suf = target.suffix
    for i in range(1, 1000):
        candidate = Path(f"{base}-{i}{suf}")
        if not candidate.exists():
            return candidate
    raise SystemExit(f"Could not find unique filename for {target}")


def normalize_downloaded_files(root: Path) -> None:
    html_errors: List[str] = []

    files = [p for p in root.rglob("*") if p.is_file()]
    for p in sorted(files, key=lambda x: str(x).lower()):
        if p.suffix.lower() in {".docx", ".pdf"}:
            continue
        if p.suffix.lower() in {".gdoc", ".gsheet", ".gslides"}:
            continue

        kind, html_error = sniff_kind_and_error(p)
        if html_error:
            html_errors.append(html_error)
            continue

        if not kind:
            continue

        target = unique_path(p.with_suffix(f".{kind}"))
        p.rename(target)
        print(f"[normalize] {p} -> {target}")

    if html_errors:
        msg = (
            "Drive download returned HTML pages (permission/login/redirect) instead of files.\n"
            "Fix: Folder + files must be 'Anyone with the link' and 'Viewer'. Test in Incognito.\n\n"
            + "\n\n".join(html_errors[:3])
        )
        raise SystemExit(msg)


def assert_has_docs(root: Path) -> None:
    docx = list(root.rglob("*.docx"))
    pdf = list(root.rglob("*.pdf"))
    if docx or pdf:
        print(f"[sync] found DOCX={len(docx)} PDF={len(pdf)}")
        return
    raise SystemExit(
        "No .docx/.pdf found after sync.\n"
        "Ensure Drive folder is public (Anyone with the link) + Viewer and files are real .docx/.pdf."
    )


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


def leading_number_key(title: str) -> Tuple[int, str]:
    m = re.match(r"^\s*(\d{1,4})\b", title)
    if m:
        return int(m.group(1)), title.lower()
    return 10**9, title.lower()


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
        sort_key = leading_number_key(title)

        out_dir_notes = OUT_NOTES / rel_dir
        out_dir_dl = OUT_DOWNLOADS / rel_dir
        out_dir_notes.mkdir(parents=True, exist_ok=True)
        out_dir_dl.mkdir(parents=True, exist_ok=True)

        if ext == ".docx":
            out_html = out_dir_notes / f"{rel_stem}.html"
            out_file = out_dir_dl / f"{rel_stem}.docx"
            entries.append(Entry("docx", title, rel_dir, rel_stem, f, out_html, out_file, sort_key))
        else:
            out_html = out_dir_notes / f"{rel_stem}.pdf.html"
            out_file = out_dir_dl / f"{rel_stem}.pdf"
            entries.append(Entry("pdf", title, rel_dir, rel_stem, f, out_html, out_file, sort_key))

    return sorted(entries, key=lambda e: (str(e.rel_dir).lower(), e.sort_key))


def rel_from(frm: Path, to: Path) -> str:
    return os.path.relpath(to, start=frm.parent).replace("\\", "/")


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


def wrap_html(*, title: str, body_html: str, home_href: str) -> str:
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
      body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;line-height:1.6;margin:0;background:#fff}
      .wrap{max-width:980px;margin:0 auto;padding:24px}
      .topbar{position:sticky;top:0;background:#fff;border-bottom:1px solid #eee}
      .topbar .wrap{display:flex;gap:12px;align-items:center;justify-content:space-between}
      .btns{display:flex;gap:10px;flex-wrap:wrap}
      a.btn{display:inline-block;padding:10px 12px;border:1px solid #ddd;border-radius:12px;text-decoration:none;color:inherit}
      a.btn:hover{background:#fafafa}
      .card{border:1px solid #eee;border-radius:16px;padding:16px}
      ul{padding-left:18px}
      img{max-width:100%;height:auto}
      table{border-collapse:collapse;width:100%;overflow-x:auto;display:block}
      th,td{border:1px solid #ddd;padding:8px}
      pre,code{background:#f6f8fa;border-radius:8px}
      pre{padding:12px;overflow:auto}
      hr{border:none;border-top:1px solid #eee;margin:18px 0}
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
    btns.append(a_home)

    topwrap.extend([title_div, btns])
    topbar.append(topwrap)

    content = soup.new_tag("div", attrs={"class": "wrap"})
    content.append(BeautifulSoup(body_html, "html.parser"))

    body.extend([topbar, content])
    html.extend([head, body])
    soup.append(html)
    return "<!doctype html>\n" + str(soup)


def build_docx(e: Entry) -> None:
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
        return {"src": rel_from(e.out_html, out_path)}

    with e.src.open("rb") as f:
        result = mammoth.convert_to_html(f, convert_image=mammoth.images.img_element(convert_image))

    dl_href = rel_from(e.out_html, e.out_file)
    body = f"""
      <div class="card">
        <p><a class="btn" href="{dl_href}">⬇️ DOCX indir</a></p>
      </div>
      <hr />
      {result.value}
    """
    e.out_html.write_text(
        wrap_html(title=e.title, body_html=body, home_href=rel_from(e.out_html, DOCS_DIR / "index.html")),
        encoding="utf-8",
    )


def build_pdf(e: Entry) -> None:
    shutil.copy2(e.src, e.out_file)
    pdf_href = rel_from(e.out_html, e.out_file)
    body = f"""
      <div class="card">
        <p><a class="btn" href="{pdf_href}">⬇️ PDF indir</a></p>
      </div>
      <hr />
      <iframe src="{pdf_href}" width="100%" height="900" style="border:1px solid #ddd; border-radius:12px;"></iframe>
    """
    e.out_html.write_text(
        wrap_html(title=e.title, body_html=body, home_href=rel_from(e.out_html, DOCS_DIR / "index.html")),
        encoding="utf-8",
    )


def build_indexes(entries: Iterable[Entry]) -> None:
    entries = list(entries)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    def group_key(e: Entry) -> str:
        return str(e.rel_dir) if str(e.rel_dir) else "Kök"

    grouped: dict[str, List[Entry]] = {}
    for e in entries:
        grouped.setdefault(group_key(e), []).append(e)

    body_lines: List[str] = [
        "<h1>FSP Notları</h1>",
        f"<p><i>Otomatik güncellendi: {now}</i></p>",
        '<div class="card"><p>Okumak için başlığa tıkla, indirmek için sağdaki linki kullan.</p></div>',
        "<h2>İçerik</h2>",
    ]

    if not entries:
        body_lines.append('<div class="card"><p>Henüz Drive’dan DOCX/PDF indirilemedi.</p></div>')
    else:
        for gname in sorted(grouped.keys(), key=lambda x: x.lower()):
            body_lines.append(f"<h3>{gname}</h3>")
            body_lines.append("<ul>")
            for e in grouped[gname]:
                read_href = rel_from(DOCS_DIR / "index.html", e.out_html)
                dl_href = rel_from(DOCS_DIR / "index.html", e.out_file)
                icon = "📖" if e.kind == "docx" else "📄"
                body_lines.append(
                    f'<li>{icon} <a href="{read_href}">{e.title}</a> · <a href="{dl_href}">⬇️ {e.kind.upper()}</a></li>'
                )
            body_lines.append("</ul>")

    body_lines += [
        "<hr />",
        "<p><a class='btn' href='./notes/index.html'>📚 Notes index</a> <a class='btn' href='./downloads/index.html'>⬇️ Downloads index</a></p>",
    ]

    (DOCS_DIR / "index.html").write_text(
        wrap_html(title="FSP Notları", body_html="\n".join(body_lines), home_href="./index.html"),
        encoding="utf-8",
    )

    notes_lines = ["<h1>Notes</h1><ul>"]
    for e in entries:
        href = rel_from(OUT_NOTES / "index.html", e.out_html)
        notes_lines.append(f'<li><a href="{href}">{e.title}</a></li>')
    notes_lines.append("</ul>")

    (OUT_NOTES / "index.html").write_text(
        wrap_html(title="Notes", body_html="\n".join(notes_lines), home_href=rel_from(OUT_NOTES / "index.html", DOCS_DIR / "index.html")),
        encoding="utf-8",
    )

    dl_lines = ["<h1>Downloads</h1><ul>"]
    for e in entries:
        href = rel_from(OUT_DOWNLOADS / "index.html", e.out_file)
        dl_lines.append(f'<li><a href="{href}">{e.title} ({e.kind.upper()})</a></li>')
    dl_lines.append("</ul>")

    (OUT_DOWNLOADS / "index.html").write_text(
        wrap_html(title="Downloads", body_html="\n".join(dl_lines), home_href=rel_from(OUT_DOWNLOADS / "index.html", DOCS_DIR / "index.html")),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
