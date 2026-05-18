#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════╗
║           🛡️  SMART UNIVERSAL DOWNLOADER  v3.0              ║
║     Safety Layer + Auto File Management + Research Papers    ║
╚══════════════════════════════════════════════════════════════╝

USAGE EXAMPLES:
  python downloader.py https://example.com/movie.mp4
  python downloader.py https://youtu.be/xxx --video
  python downloader.py https://youtu.be/xxx --audio
  python downloader.py https://example.com/photo.jpg
  python downloader.py https://example.com/archive.zip
  python downloader.py --paper "Attention is All You Need"
  python downloader.py --paper-list my_papers.txt

AUTO FOLDER STRUCTURE:
  Downloads/
  ├── videos/        ← mp4, mkv, avi, mov, webm ...
  ├── audio/         ← mp3, wav, flac, aac, ogg ...
  ├── images/        ← jpg, png, gif, svg, webp ...
  ├── documents/     ← pdf, docx, pptx, xlsx, txt ...
  ├── papers/        ← Research papers (PDF)
  ├── archives/      ← zip, tar, gz, rar, 7z ...
  ├── ebooks/        ← epub, mobi, azw ...
  ├── code/          ← py, js, html, css, json ...
  └── others/        ← anything unrecognized
"""

import argparse
import hashlib
import json
import mimetypes
import os
import re
import subprocess
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path


# ─────────────────────────────────────────────────────────────
#  AUTO-INSTALL DEPENDENCIES
# ─────────────────────────────────────────────────────────────
def _install(pkg):
    subprocess.check_call([sys.executable, "-m", "pip", "install", pkg, "-q"])

for _pkg in ["requests", "tqdm", "yt-dlp", "colorama"]:
    try:
        __import__(_pkg.replace("-", "_"))
    except ImportError:
        print(f"📦 Installing {_pkg}...")
        _install(_pkg)

import requests
from colorama import Fore, Style, init
from tqdm import tqdm

init(autoreset=True)


# ─────────────────────────────────────────────────────────────
#  PRETTY CONSOLE HELPERS
# ─────────────────────────────────────────────────────────────
def info(m):    print(Fore.CYAN    + "ℹ  " + Style.RESET_ALL + str(m))
def ok(m):      print(Fore.GREEN   + "✅ " + Style.RESET_ALL + str(m))
def warn(m):    print(Fore.YELLOW  + "⚠  " + Style.RESET_ALL + str(m))
def err(m):     print(Fore.RED     + "❌ " + Style.RESET_ALL + str(m))
def blocked(m): print(Fore.RED + Style.BRIGHT + "🚫 BLOCKED: " + Style.RESET_ALL + str(m))
def hdr(m):
    bar = "═" * 58
    print(f"\n{Fore.MAGENTA}{bar}\n  {m}\n{bar}{Style.RESET_ALL}")


# ─────────────────────────────────────────────────────────────
#  🛡️  SAFETY LAYER
# ─────────────────────────────────────────────────────────────
class SafetyGuard:
    """
    Multi-level safety filter. Checks file extensions, MIME types,
    URL patterns, and magic bytes to block harmful downloads.
    """

    # Extensions that can execute, infect, or damage systems
    DANGEROUS_EXTENSIONS = {
        # Windows executables & scripts
        ".exe", ".msi", ".bat", ".cmd", ".com", ".scr", ".pif",
        ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh", ".ps1",
        ".ps2", ".psm1", ".psd1", ".reg", ".inf", ".lnk", ".hta",
        # Linux/Unix dangerous
        ".sh", ".bash", ".run", ".deb", ".rpm", ".bin",
        # macOS dangerous
        ".app", ".pkg", ".dmg", ".command", ".tool",
        # Office macros / exploits
        ".xlsm", ".xlsb", ".docm", ".dotm", ".pptm", ".potm",
        # Archive bombs (checked separately by size ratio)
        # Android APK (can be malicious)
        ".apk",
        # Java web start
        ".jnlp",
        # Compiled code that can run
        ".dll", ".so", ".dylib",
    }

    # MIME types that are always dangerous
    DANGEROUS_MIMES = {
        "application/x-msdownload",
        "application/x-msdos-program",
        "application/x-executable",
        "application/x-sh",
        "application/x-shellscript",
        "application/x-bat",
        "application/x-msi",
        "application/vnd.microsoft.portable-executable",
    }

    # URL patterns that suggest malware / phishing sites
    SUSPICIOUS_URL_PATTERNS = [
        r"bit\.ly/[0-9A-Za-z]+",           # Unresolved short links (warn only)
        r"[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}/.*\.(exe|msi|bat|sh)",
        r"(free|crack|keygen|serial|patch|hack|cheat|warez)",
        r"(download-free|nulled|pirat)",
    ]

    # Magic bytes (file headers) for executables
    MAGIC_BYTES = {
        b"MZ":           "Windows PE Executable",
        b"\x7fELF":      "Linux ELF Executable",
        b"\xca\xfe\xba\xbe": "macOS Mach-O Binary",
        b"#!":           "Shell Script",
    }

    # Max allowed file size (500 MB default – adjustable)
    MAX_SIZE_BYTES = 500 * 1024 * 1024

    def __init__(self, strict: bool = False):
        self.strict = strict   # strict=True blocks even warnings

    def check_url(self, url: str) -> tuple[bool, str]:
        """Returns (is_safe, reason). safe=True means OK to proceed."""
        url_lower = url.lower()

        # Block dangerous extensions in URL path
        parsed = urllib.parse.urlparse(url)
        path_ext = Path(parsed.path).suffix.lower()
        if path_ext in self.DANGEROUS_EXTENSIONS:
            return False, f"Dangerous file extension in URL: '{path_ext}'"

        # Check suspicious URL patterns
        for pat in self.SUSPICIOUS_URL_PATTERNS:
            match = re.search(pat, url_lower)
            if match:
                reason = f"Suspicious URL pattern detected: '{match.group()}'"
                if self.strict:
                    return False, reason
                else:
                    warn(reason + " — proceeding with caution.")

        return True, "OK"

    def check_response_headers(self, response: requests.Response) -> tuple[bool, str]:
        """Check Content-Type and Content-Length from response headers."""
        content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
        content_len  = int(response.headers.get("Content-Length", 0))

        # Check MIME type
        if content_type in self.DANGEROUS_MIMES:
            return False, f"Dangerous MIME type: '{content_type}'"

        # Check file size
        if content_len > self.MAX_SIZE_BYTES:
            size_mb = content_len / (1024 * 1024)
            return False, f"File too large: {size_mb:.1f} MB (limit {self.MAX_SIZE_BYTES/1024/1024:.0f} MB). Use --allow-large to override."

        return True, "OK"

    def check_magic_bytes(self, chunk: bytes) -> tuple[bool, str]:
        """Inspect the first bytes of the downloaded data."""
        for magic, label in self.MAGIC_BYTES.items():
            if chunk.startswith(magic):
                return False, f"File content looks like: {label}"
        return True, "OK"

    def is_safe_extension(self, ext: str) -> bool:
        return ext.lower() not in self.DANGEROUS_EXTENSIONS


# ─────────────────────────────────────────────────────────────
#  📁  SMART FILE MANAGER
# ─────────────────────────────────────────────────────────────
class FileManager:
    """
    Automatically categorises every download and saves it
    to the right sub-folder under the base Downloads directory.
    """

    BASE = "Downloads"

    CATEGORIES = {
        "videos":    {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv",
                      ".webm", ".m4v", ".3gp", ".ogv", ".ts", ".vob"},
        "audio":     {".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a",
                      ".wma", ".opus", ".aiff", ".alac"},
        "images":    {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".svg",
                      ".webp", ".tiff", ".ico", ".heic", ".avif"},
        "documents": {".pdf", ".doc", ".docx", ".odt", ".txt", ".rtf",
                      ".pptx", ".ppt", ".xlsx", ".xls", ".csv", ".md"},
        "archives":  {".zip", ".tar", ".gz", ".bz2", ".xz", ".rar",
                      ".7z", ".tar.gz", ".tar.bz2", ".tgz"},
        "ebooks":    {".epub", ".mobi", ".azw", ".azw3", ".fb2", ".lit"},
        "code":      {".py", ".js", ".ts", ".html", ".css", ".json",
                      ".xml", ".yaml", ".yml", ".java", ".c", ".cpp",
                      ".h", ".go", ".rs", ".rb", ".php", ".sql"},
        "datasets":  {".npy", ".npz", ".h5", ".hdf5", ".parquet",
                      ".feather", ".pkl", ".pt", ".pth", ".onnx"},
    }

    # Reverse lookup: ext → category
    _EXT_MAP: dict[str, str] = {}

    def __init__(self, base: str = None):
        self.BASE = base or self.BASE
        for cat, exts in self.CATEGORIES.items():
            for ext in exts:
                self._EXT_MAP[ext] = cat

    def categorise(self, filename: str, override_category: str = None) -> str:
        if override_category:
            return override_category
        ext = Path(filename).suffix.lower()
        return self._EXT_MAP.get(ext, "others")

    def get_folder(self, filename: str, override_category: str = None) -> Path:
        cat = self.categorise(filename, override_category)
        folder = Path(self.BASE) / cat
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def resolve_path(self, filename: str, override_category: str = None) -> Path:
        folder = self.get_folder(filename, override_category)
        target = folder / filename
        # Avoid overwriting: append counter if file exists
        if target.exists():
            stem = target.stem
            suffix = target.suffix
            for i in range(1, 9999):
                candidate = folder / f"{stem}_{i}{suffix}"
                if not candidate.exists():
                    return candidate
        return target

    def print_tree(self):
        """Print the current Downloads folder structure."""
        base = Path(self.BASE)
        if not base.exists():
            warn("Downloads folder is empty.")
            return
        hdr("📁 Downloads Folder Structure")
        total_files = 0
        total_size  = 0
        for cat_dir in sorted(base.iterdir()):
            if not cat_dir.is_dir():
                continue
            files = list(cat_dir.iterdir())
            size  = sum(f.stat().st_size for f in files if f.is_file())
            total_files += len(files)
            total_size  += size
            icon = {
                "videos": "🎬", "audio": "🎵", "images": "🖼 ",
                "documents": "📄", "papers": "📚", "archives": "📦",
                "ebooks": "📖", "code": "💻", "datasets": "📊", "others": "📂",
            }.get(cat_dir.name, "📂")
            print(f"  {icon}  {cat_dir.name:<14}  {len(files):>4} file(s)   {_fmt_size(size)}")
        print(f"\n  Total: {total_files} file(s)  —  {_fmt_size(total_size)}")
        print(f"  Path : {base.resolve()}\n")


def _fmt_size(b: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} TB"

def _sanitize(name: str, max_len: int = 120) -> str:
    keep = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ._-,()")
    return "".join(c if c in keep else "_" for c in name)[:max_len].strip()


# ─────────────────────────────────────────────────────────────
#  ⬇️  CORE FILE DOWNLOADER
# ─────────────────────────────────────────────────────────────
def download_file(
    url: str,
    output_name: str = None,
    override_category: str = None,
    guard: "SafetyGuard" = None,
    fm: "FileManager" = None,
    allow_large: bool = False,
) -> bool:

    guard = guard or SafetyGuard()
    fm    = fm    or FileManager()

    # ── 1. Safety: check URL ──────────────────────────────────
    safe, reason = guard.check_url(url)
    if not safe:
        blocked(reason)
        return False

    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; SmartDownloader/3.0)"}
        with requests.get(url, stream=True, timeout=30, headers=headers) as response:
            response.raise_for_status()

            # ── 2. Safety: check headers ──────────────────────
            if not allow_large:
                safe, reason = guard.check_response_headers(response)
                if not safe:
                    blocked(reason)
                    return False

            # Determine filename
            if not output_name:
                cd = response.headers.get("Content-Disposition", "")
                if "filename=" in cd:
                    output_name = cd.split("filename=")[-1].strip(' "\'')
                else:
                    output_name = url.split("/")[-1].split("?")[0] or "download"
                    if "." not in output_name:
                        # Try to guess ext from MIME
                        ctype = response.headers.get("Content-Type", "").split(";")[0]
                        ext = mimetypes.guess_extension(ctype) or ""
                        output_name += ext

            output_name = _sanitize(output_name)
            filepath    = fm.resolve_path(output_name, override_category)
            category    = fm.categorise(output_name, override_category)

            info(f"Category  → {Fore.YELLOW}{category}{Style.RESET_ALL}")
            info(f"Saving to → {Fore.YELLOW}{filepath}{Style.RESET_ALL}")

            total = int(response.headers.get("content-length", 0))
            first_chunk_checked = False

            with open(filepath, "wb") as f, tqdm(
                desc=f"  {output_name[:45]}",
                total=total, unit="B", unit_scale=True, unit_divisor=1024,
                bar_format="{l_bar}{bar:30}{r_bar}",
            ) as bar:
                for chunk in response.iter_content(chunk_size=8192):
                    # ── 3. Safety: check magic bytes (first chunk only)
                    if not first_chunk_checked and chunk:
                        safe, reason = guard.check_magic_bytes(chunk)
                        if not safe:
                            f.close()
                            filepath.unlink(missing_ok=True)
                            blocked(reason)
                            return False
                        first_chunk_checked = True

                    f.write(chunk)
                    bar.update(len(chunk))

        ok(f"Done! → {filepath}")
        return True

    except requests.exceptions.HTTPError as e:
        err(f"HTTP Error: {e}")
    except requests.exceptions.ConnectionError:
        err("Connection failed. Check your internet or the URL.")
    except requests.exceptions.Timeout:
        err("Request timed out.")
    except Exception as e:
        err(f"Unexpected error: {e}")
    return False


# ─────────────────────────────────────────────────────────────
#  🎬  VIDEO / AUDIO DOWNLOADER (yt-dlp)
# ─────────────────────────────────────────────────────────────
def download_media(
    url: str,
    audio_only: bool = False,
    output_name: str = None,
    fm: "FileManager" = None,
    guard: "SafetyGuard" = None,
):
    import yt_dlp
    guard = guard or SafetyGuard()
    fm    = fm    or FileManager()

    safe, reason = guard.check_url(url)
    if not safe:
        blocked(reason)
        return

    category = "audio" if audio_only else "videos"
    folder   = fm.get_folder("placeholder.mp4", override_category=category)

    template = str(folder / "%(title)s.%(ext)s")
    if output_name:
        template = str(folder / f"{_sanitize(output_name)}.%(ext)s")

    ydl_opts = {
        "outtmpl": template,
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [
            lambda d: print(
                f"\r  ⬇  {d.get('_percent_str','').strip():>6}  "
                f"{d.get('_speed_str','').strip():>12}  "
                f"ETA {d.get('eta','?')}s   ",
                end="", flush=True,
            ) if d["status"] == "downloading" else None
        ],
    }

    if audio_only:
        ydl_opts.update({
            "format": "bestaudio/best",
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
        })
        info(f"Downloading audio → {folder}")
    else:
        ydl_opts["format"] = "bestvideo+bestaudio/best"
        info(f"Downloading video → {folder}")

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        print()
        ok("Media download complete!")
    except Exception as e:
        print()
        err(f"yt-dlp error: {e}")


# ─────────────────────────────────────────────────────────────
#  📚  RESEARCH PAPER DOWNLOADER
# ─────────────────────────────────────────────────────────────
class PaperDownloader:
    S2_API        = "https://api.semanticscholar.org/graph/v1/paper/search"
    ARXIV_API     = "https://export.arxiv.org/api/query"
    UNPAYWALL_API = "https://api.unpaywall.org/v2/"
    CROSSREF_API  = "https://api.crossref.org/works"
    HEADERS       = {"User-Agent": "SmartDownloader/3.0 (education; non-commercial)"}

    def __init__(self, fm: FileManager, guard: SafetyGuard):
        self.fm     = fm
        self.guard  = guard
        self.log    = []

    # ── Search APIs ──────────────────────────────────────────
    def _semantic_scholar(self, title: str) -> dict | None:
        try:
            r = requests.get(self.S2_API, headers=self.HEADERS, timeout=10, params={
                "query": title,
                "fields": "title,authors,year,openAccessPdf,externalIds,abstract",
                "limit": 5,
            })
            r.raise_for_status()
            items = r.json().get("data", [])
            if items:
                p = items[0]
                return {
                    "title":    p.get("title", title),
                    "authors":  ", ".join(a["name"] for a in (p.get("authors") or [])[:3]),
                    "year":     str(p.get("year", "")),
                    "pdf_url":  (p.get("openAccessPdf") or {}).get("url"),
                    "arxiv_id": (p.get("externalIds") or {}).get("ArXiv"),
                    "abstract": (p.get("abstract") or "")[:300],
                    "source":   "Semantic Scholar",
                }
        except Exception as e:
            warn(f"Semantic Scholar: {e}")
        return None

    def _arxiv(self, title: str, arxiv_id: str = None) -> dict | None:
        try:
            query = f"id:{arxiv_id}" if arxiv_id else f"ti:{urllib.parse.quote(title)}"
            r = requests.get(self.ARXIV_API, headers=self.HEADERS, timeout=10,
                             params={"search_query": query, "max_results": 3})
            r.raise_for_status()
            ns   = {"a": "http://www.w3.org/2005/Atom"}
            root = ET.fromstring(r.text)
            for e in root.findall("a:entry", ns):
                aid = e.find("a:id", ns).text.split("/")[-1]
                return {
                    "title":    (e.find("a:title", ns).text or title).strip(),
                    "authors":  ", ".join(
                        (a.find("a:name", ns).text or "")
                        for a in e.findall("a:author", ns)[:3]
                    ),
                    "year":     (e.find("a:published", ns).text or "")[:4],
                    "pdf_url":  f"https://arxiv.org/pdf/{aid}.pdf",
                    "arxiv_id": aid,
                    "abstract": (e.find("a:summary", ns).text or "")[:300].strip(),
                    "source":   "arXiv",
                }
        except Exception as e:
            warn(f"arXiv: {e}")
        return None

    def _crossref_doi(self, title: str) -> str | None:
        try:
            r = requests.get(self.CROSSREF_API, headers=self.HEADERS, timeout=10,
                             params={"query.title": title, "rows": 1, "select": "DOI"})
            r.raise_for_status()
            items = r.json().get("message", {}).get("items", [])
            if items:
                return items[0].get("DOI")
        except Exception:
            pass
        return None

    def _unpaywall(self, doi: str) -> str | None:
        try:
            r = requests.get(f"{self.UNPAYWALL_API}{doi}?email=smartdl@example.com",
                             headers=self.HEADERS, timeout=10)
            r.raise_for_status()
            loc = r.json().get("best_oa_location") or {}
            return loc.get("url_for_pdf")
        except Exception:
            pass
        return None

    # ── Full search pipeline ──────────────────────────────────
    def find(self, title: str) -> dict | None:
        meta = self._semantic_scholar(title)
        if not meta or not meta.get("pdf_url"):
            arxiv = self._arxiv(title, (meta or {}).get("arxiv_id"))
            if arxiv:
                meta = meta or arxiv
                meta["pdf_url"] = meta.get("pdf_url") or arxiv.get("pdf_url")
        if meta and not meta.get("pdf_url"):
            doi = self._crossref_doi(title)
            if doi:
                meta["pdf_url"] = self._unpaywall(doi)
        return meta

    # ── Download one paper ────────────────────────────────────
    def download(self, title: str) -> str:
        meta   = self.find(title)
        status = "not_found"

        if not meta:
            err(f'No results found for "{title}"')
            self.log.append({"query": title, "status": status})
            return status

        print(f"\n  📄 {Fore.WHITE}{meta['title']}{Style.RESET_ALL}")
        if meta.get("authors"): print(f"  👤 {meta['authors']}")
        if meta.get("year"):    print(f"  📅 {meta['year']}  via {meta.get('source','')}")
        if meta.get("abstract"):print(f"  📝 {meta['abstract'][:180]}…")

        pdf_url = meta.get("pdf_url")
        if not pdf_url:
            warn("Paper found but no free PDF available.")
            self.log.append({"query": title, "status": "no_pdf", "meta": meta})
            return "no_pdf"

        safe_title = _sanitize(meta["title"])
        year_tag   = f"_{meta['year']}" if meta.get("year") else ""
        filename   = f"{safe_title}{year_tag}.pdf"

        success = download_file(
            url=pdf_url,
            output_name=filename,
            override_category="papers",
            guard=self.guard,
            fm=self.fm,
        )
        status = "ok" if success else "failed"
        self.log.append({"query": title, "status": status, "meta": meta})
        return status

    # ── Batch download ────────────────────────────────────────
    def batch(self, titles: list[str]):
        hdr(f"📚 Batch Paper Download  ({len(titles)} papers)")
        for i, t in enumerate(titles, 1):
            print(f"\n[{i}/{len(titles)}]  {Fore.CYAN}{t}{Style.RESET_ALL}")
            self.download(t)
            time.sleep(1.2)   # polite API rate
        self._summary()

    def _summary(self):
        hdr("Download Summary")
        counts = {"ok": 0, "skipped": 0, "no_pdf": 0, "failed": 0, "not_found": 0}
        for r in self.log:
            counts[r["status"]] = counts.get(r["status"], 0) + 1

        print(f"  ✅  Downloaded  : {counts['ok']}")
        print(f"  ⏭   Skipped     : {counts['skipped']}")
        print(f"  🔒  No free PDF : {counts['no_pdf']}")
        print(f"  ❌  Failed/404  : {counts['failed'] + counts['not_found']}")

        no_pdf_items = [r for r in self.log if r["status"] == "no_pdf"]
        if no_pdf_items:
            print(Fore.YELLOW + "\n  Papers found but paywalled:")
            for r in no_pdf_items:
                m = r.get("meta") or {}
                print(f"    • {m.get('title', r['query'])}")

        # Save JSON log
        log_path = Path(self.fm.BASE) / "papers" / "download_log.json"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "w", encoding="utf-8") as f:
            json.dump(self.log, f, indent=2, default=str)
        print(Style.RESET_ALL + f"\n  📋 Log saved → {log_path}\n")


# ─────────────────────────────────────────────────────────────
#  🚀  MAIN CLI
# ─────────────────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(
        description="🛡️  Smart Universal Downloader v3.0",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # General download
    p.add_argument("url",           nargs="?",  help="URL to download")
    p.add_argument("-o", "--output",default=None, help="Custom output filename")
    p.add_argument("--video",       action="store_true", help="Force video download (yt-dlp)")
    p.add_argument("--audio",       action="store_true", help="Download audio as MP3")
    p.add_argument("--allow-large", action="store_true", help="Skip the 500 MB file-size limit")

    # Research papers
    p.add_argument("--paper",       metavar="TITLE", help='Download one paper: --paper "BERT"')
    p.add_argument("--paper-list",  metavar="FILE",  help="Text file, one paper title per line")

    # File manager
    p.add_argument("--tree",        action="store_true", help="Show Downloads folder tree and exit")
    p.add_argument("--downloads-dir", default="Downloads", help="Base downloads folder (default: Downloads/)")

    # Safety
    p.add_argument("--strict",      action="store_true", help="Enable strict safety mode (block on any warning)")

    args = p.parse_args()

    guard = SafetyGuard(strict=args.strict)
    fm    = FileManager(base=args.downloads_dir)

    # ── Show folder tree ──────────────────────────────────────
    if args.tree:
        fm.print_tree()
        return

    # ── Research paper mode ───────────────────────────────────
    if args.paper or args.paper_list:
        pd = PaperDownloader(fm=fm, guard=guard)

        if args.paper_list:
            path = Path(args.paper_list)
            if not path.exists():
                err(f"File not found: {args.paper_list}")
                sys.exit(1)
            titles = [
                l.strip() for l in path.read_text(encoding="utf-8").splitlines()
                if l.strip() and not l.startswith("#")
            ]
            if not titles:
                err("Paper list is empty.")
                sys.exit(1)
            pd.batch(titles)
        else:
            hdr("📚 Research Paper Download")
            pd.download(args.paper)
            pd._summary()
        return

    # ── URL download mode ─────────────────────────────────────
    if not args.url:
        p.print_help()
        return

    print()
    info(f"URL: {args.url}")

    VIDEO_HOSTS = {
        "youtube.com", "youtu.be", "vimeo.com", "dailymotion.com",
        "twitter.com", "x.com", "instagram.com", "facebook.com",
        "tiktok.com", "twitch.tv", "bilibili.com",
    }
    is_media = args.video or args.audio or any(h in args.url for h in VIDEO_HOSTS)

    if is_media:
        download_media(
            url=args.url,
            audio_only=args.audio,
            output_name=args.output,
            fm=fm,
            guard=guard,
        )
    else:
        download_file(
            url=args.url,
            output_name=args.output,
            guard=guard,
            fm=fm,
            allow_large=args.allow_large,
        )

 