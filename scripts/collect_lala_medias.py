#!/usr/bin/env python3
"""Collect LALACHAN generated videos into a Git/LFS media website.

The collector intentionally keeps the workflow simple and auditable:

1. Scan only the canonical LALACHAN/Videos folder for videos.
2. Deduplicate videos by SHA-256 and by story-family variants.
3. Exclude processed subtitle/logo/portrait/burned-output variants.
4. Copy or hard-link clean generated videos into media/videos/.
5. Match LazyEdit subtitle sidecars and render transcripts below each video.
6. Generate a static index, per-video pages, and a JSON manifest.

The script uses only the Python standard library plus ffmpeg/ffprobe when
available. It does not modify the source folders.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]

VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm"}
SUBTITLE_EXTS = {".srt", ".vtt", ".md"}

HOME = Path.home()
LALACHAN_ROOT = Path(os.environ.get("LALACHAN_ROOT", str(REPO_ROOT.parent / "LALACHAN"))).expanduser()
LAZYEDIT_DATA_ROOT = Path(os.environ.get("LAZYEDIT_DATA_ROOT", str(HOME / "DiskMech" / "Projects" / "lazyedit" / "DATA"))).expanduser()
VIDEO_BASE_URL = os.environ.get("LALAMEDIAS_VIDEO_BASE_URL", "").rstrip("/")

VIDEO_SOURCES = [
    ("lalachan-videos", LALACHAN_ROOT / "Videos"),
]

SUBTITLE_SOURCES = [
    ("lazyedit-data", LAZYEDIT_DATA_ROOT),
]

PREFERRED_SOURCE_ORDER = {
    "lalachan-videos": 0,
}

GENERATED_VIDEO_PREFIXES = (
    "2026-",
    "aginti_",
    "anniversary_",
    "aya_",
    "big_",
    "biological_",
    "canyon_",
    "cold_",
    "desert_",
    "dragon_",
    "earth_",
    "episode",
    "female_",
    "firefly_",
    "football_",
    "forest_",
    "guan_",
    "mars_",
    "meguro_",
    "olive_",
    "red_",
    "restaurant_",
    "snow_",
    "tokushima_",
    "treasure_",
    "typhoon_",
    "uma_",
    "v03c",
)

DERIVATIVE_VIDEO_PATTERNS = (
    "subtitle_removed",
    "subtitles_logo",
    "subtitle_logo",
    "_subtitles",
    "-subtitles",
    "_subtitle",
    "-subtitle",
    "_logo",
    "-logo",
    "portrait_blurfill",
    "portrait_fg",
    "blurfill",
    "hq_publish",
    "_publish",
    "clean_no_subtitle",
    "no_subtitle",
)


@dataclass
class VideoCandidate:
    path: Path
    source: str
    size: int
    mtime: float
    sha256: str | None = None


@dataclass
class SubtitleCandidate:
    path: Path
    source: str
    mtime: float


@dataclass
class MediaItem:
    sha256: str
    canonical: VideoCandidate
    sources: list[VideoCandidate] = field(default_factory=list)
    subtitles: list[SubtitleCandidate] = field(default_factory=list)
    slug: str = ""
    title: str = ""
    video_rel: str = ""
    thumb_rel: str = ""
    duration: float | None = None
    width: int | None = None
    height: int | None = None
    transcript_rel: str | None = None
    subtitle_rel_files: list[str] = field(default_factory=list)
    page_rel: str = ""
    description: str = ""
    publish_category: str = ""


def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def is_video(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in VIDEO_EXTS


def is_subtitle(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in SUBTITLE_EXTS


def should_include_video(path: Path, source: str) -> bool:
    try:
        size = path.stat().st_size
    except OSError:
        return False
    if size < 100_000:
        return False
    name = path.name.lower()
    if name.startswith("."):
        return False
    if not name.startswith(GENERATED_VIDEO_PREFIXES):
        return False
    if re.fullmatch(r"\d+\.mp4", name):
        return False
    if name.startswith(("final_video", "final-video", "user-upload", "video-")):
        return False
    if any(pattern in name for pattern in DERIVATIVE_VIDEO_PATTERNS):
        return False
    if source != "lalachan-videos":
        return False
    try:
        return path.parent.resolve() == (LALACHAN_ROOT / "Videos").resolve()
    except OSError:
        return False


def walk_files(root: Path) -> Iterable[Path]:
    if not root.exists():
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in {".git", "node_modules", "__pycache__", "temp"}]
        for filename in filenames:
            yield Path(dirpath) / filename


def collect_candidates() -> tuple[list[VideoCandidate], list[SubtitleCandidate]]:
    videos: list[VideoCandidate] = []
    subtitles: list[SubtitleCandidate] = []
    for source, root in VIDEO_SOURCES:
        if not root.exists():
            continue
        for path in walk_files(root):
            if is_video(path) and should_include_video(path, source):
                try:
                    stat = path.stat()
                except OSError:
                    continue
                videos.append(VideoCandidate(path=path, source=source, size=stat.st_size, mtime=stat.st_mtime))
    for source, root in SUBTITLE_SOURCES:
        if not root.exists():
            continue
        for path in walk_files(root):
            if is_subtitle(path):
                try:
                    stat = path.stat()
                except OSError:
                    continue
                subtitles.append(SubtitleCandidate(path=path, source=source, mtime=stat.st_mtime))
    return videos, subtitles


def title_from_path(path: Path) -> str:
    stem = path.stem
    if stem.lower().startswith("v03c"):
        date_match = re.search(r"(20\d{2})[_-](\d{2})[_-](\d{2})", stem)
        if date_match:
            return f"LALACHAN Generated Clip {date_match.group(1)}-{date_match.group(2)}-{date_match.group(3)}"
        return "LALACHAN Generated Clip"
    stem = re.sub(r"(?i)20\d{2}[-_]\d{2}[-_]\d{2}(?:t\d{6}(?:\.\d+)?)?", " ", stem)
    stem = re.sub(r"(?i)(?:^|[_\-\s])(?:seedance|mini|fast|duanpian|chenjinshi|cheapest|uploaded_images_only|xyq|visual|song_locked|final|completed)(?=$|[_\-\s])", " ", stem)
    stem = re.sub(r"(?i)(?:^|[_\-\s])(?:4x3|9x16|10s|15s|30s|34s|68s|90s|01)(?=$|[_\-\s])", " ", stem)
    stem = re.sub(r"_\d{4}_\d{2}_\d{2}_\d{2}_\d{2}_\d{2}_COMPLETED$", "", stem)
    stem = re.sub(r"_COMPLETED$", "", stem, flags=re.I)
    stem = stem.replace("_", " ").replace("-", " ")
    stem = re.sub(r"\s+", " ", stem).strip()
    words = []
    replacements = {
        "aginti": "AgInTi",
        "ai": "AI",
        "aya": "Aya",
        "ayachan": "Aya Chan",
        "hikari": "Hikari",
        "mv": "MV",
        "raraxia": "RaraXia",
        "sasa": "Sasa",
        "sasakun": "Sasa Kun",
        "zhuangzi": "Zhuangzi",
        "2d": "2D",
    }
    for word in stem.split():
        lower = word.lower()
        words.append(replacements.get(lower, word[:1].upper() + word[1:]))
    title = " ".join(words)
    return title or "LALACHAN Video"


def family_key(path: Path) -> str:
    text = path.stem.lower()
    text = re.sub(r"\b20\d{2}[-_]\d{2}[-_]\d{2}\b", " ", text)
    text = re.sub(r"(?i)(portrait_blurfill.*|portrait_fg\d+.*|subtitle_space.*)", " ", text)
    for token in [
        "subtitles_logo",
        "subtitle_logo",
        "subtitles",
        "subtitle",
        "logo",
        "song_locked",
        "uploaded_images_only",
        "xyq",
        "visual",
        "final",
        "hq",
        "publish",
        "seedance",
        "mini",
        "fast",
        "duanpian",
        "chenjinshi",
        "4x3",
        "9x16",
        "15s",
        "30s",
        "10s",
    ]:
        text = re.sub(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", " ", text)
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text or path.stem.lower()


def infer_publish_category(path: Path, title: str) -> str:
    tokens = set(re.split(r"[^a-z0-9]+", f"{path.stem} {title}".lower()))
    if tokens & {"mv", "musia", "hikari", "song", "dance"}:
        return "lalamv"
    if tokens & {"lazyingart", "lightmind", "aginti"}:
        return "lalachan"
    return "lalachan"


def description_from_title(title: str, category: str) -> str:
    if category == "lalamv":
        return f"{title} is a LALACHAN character music video with clean source video and text subtitles shown below when available."
    return f"{title} is a generated LALACHAN story video. The page keeps the video clean and renders matched text subtitles below the player when available."


def slugify(title: str, sha: str) -> str:
    text = title.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    if not text:
        text = "video"
    text = text[:72].strip("-")
    return f"{text}-{sha[:8]}"


def canonical_sort_key(c: VideoCandidate) -> tuple[int, int, int, str]:
    source_rank = PREFERRED_SOURCE_ORDER.get(c.source, 99)
    name = c.path.name.lower()
    variant_penalty = 0
    if "song_locked" in name:
        variant_penalty -= 8
    if "_final" in name or "-final" in name:
        variant_penalty -= 5
    if "uploaded_images_only" in name:
        variant_penalty -= 3
    if "_xyq" in name or "-xyq" in name:
        variant_penalty += 2
    if "visual" in name:
        variant_penalty += 5
    edited_penalty = 8 if "edited" in str(c.path).lower() else 0
    return (source_rank + variant_penalty + edited_penalty, -len(c.path.stem), -int(c.mtime), str(c.path))


def group_by_hash(videos: list[VideoCandidate]) -> list[MediaItem]:
    by_hash: dict[str, list[VideoCandidate]] = {}
    for idx, candidate in enumerate(videos, 1):
        candidate.sha256 = sha256_file(candidate.path)
        by_hash.setdefault(candidate.sha256, []).append(candidate)
        if idx % 25 == 0:
            print(f"hashed {idx}/{len(videos)} videos", file=sys.stderr)

    hash_items: list[MediaItem] = []
    for sha, group in by_hash.items():
        canonical = sorted(group, key=canonical_sort_key)[0]
        title = title_from_path(canonical.path)
        slug = slugify(title, sha)
        category = infer_publish_category(canonical.path, title)
        hash_items.append(
            MediaItem(
                sha256=sha,
                canonical=canonical,
                sources=sorted(group, key=canonical_sort_key),
                title=title,
                slug=slug,
                description=description_from_title(title, category),
                publish_category=category,
            )
        )

    by_family: dict[str, list[MediaItem]] = {}
    for item in hash_items:
        by_family.setdefault(family_key(item.canonical.path), []).append(item)

    items: list[MediaItem] = []
    for group in by_family.values():
        chosen = sorted(group, key=lambda item: canonical_sort_key(item.canonical))[0]
        merged_sources: list[VideoCandidate] = []
        seen_paths: set[Path] = set()
        for member in sorted(group, key=lambda item: canonical_sort_key(item.canonical)):
            for source in member.sources:
                if source.path not in seen_paths:
                    merged_sources.append(source)
                    seen_paths.add(source.path)
        chosen.sources = merged_sources
        items.append(chosen)
    items.sort(key=lambda item: (-item.canonical.mtime, item.title.lower()))
    return items


def subtitle_priority(path: Path) -> tuple[int, float, str]:
    name = path.name.lower()
    if "mixed_polished" in name:
        rank = 0
    elif "polished" in name:
        rank = 1
    elif "mixed" in name:
        rank = 2
    elif "caption" in name:
        rank = 3
    elif path.suffix.lower() == ".srt":
        rank = 4
    elif path.suffix.lower() == ".vtt":
        rank = 5
    else:
        rank = 6
    return (rank, -path.stat().st_mtime, path.name)


def build_lazyedit_video_index(lazy_videos: list[VideoCandidate], subtitles: list[SubtitleCandidate]) -> tuple[dict[str, list[Path]], list[SubtitleCandidate]]:
    folder_subtitles: dict[Path, list[SubtitleCandidate]] = {}
    for subtitle in subtitles:
        folder_subtitles.setdefault(subtitle.path.parent, []).append(subtitle)

    by_hash: dict[str, list[Path]] = {}
    for candidate in lazy_videos:
        sha = candidate.sha256 or sha256_file(candidate.path)
        candidates = folder_subtitles.get(candidate.path.parent, [])
        if candidates:
            by_hash.setdefault(sha, []).extend(s.path for s in candidates)
    all_subtitles = subtitles
    return by_hash, all_subtitles


TOKEN_STOPWORDS = {
    "caption",
    "completed",
    "data",
    "final",
    "home",
    "lazyedit",
    "logo",
    "media",
    "mixed",
    "polished",
    "projects",
    "projectslfs",
    "session",
    "subtitle",
    "subtitles",
    "user",
    "upload",
    "video",
    "videos",
}


def stem_tokens(path: Path) -> set[str]:
    """Return meaningful match tokens from local names only.

    Never use the full absolute path for fuzzy subtitle matching: generic
    home/project folders would make unrelated media look related.
    """

    text = f"{path.parent.name} {path.stem}".lower()
    tokens = set()
    for token in re.split(r"[^a-z0-9]+", text):
        if len(token) < 4:
            continue
        if token in TOKEN_STOPWORDS:
            continue
        if token.isdigit():
            continue
        tokens.add(token)
    return tokens


def attach_subtitles(items: list[MediaItem], lazy_videos: list[VideoCandidate], subtitles: list[SubtitleCandidate]) -> None:
    hash_to_subs, all_subs = build_lazyedit_video_index(lazy_videos, subtitles)
    for item in items:
        chosen_paths: list[Path] = []
        if item.sha256 in hash_to_subs:
            chosen_paths.extend(hash_to_subs[item.sha256])

        if not chosen_paths:
            tokens = stem_tokens(item.canonical.path)
            scored: list[tuple[int, SubtitleCandidate]] = []
            for sub in all_subs:
                sub_tokens = stem_tokens(sub.path)
                shared = tokens & sub_tokens
                strong_shared = [t for t in shared if len(t) >= 7]
                overlap = len(shared)
                if overlap >= 2 or strong_shared:
                    scored.append((overlap, sub))
            scored.sort(key=lambda pair: (-pair[0], subtitle_priority(pair[1].path)))
            chosen_paths.extend(s.path for _, s in scored[:4])

        unique: dict[Path, SubtitleCandidate] = {}
        for path in chosen_paths:
            try:
                stat = path.stat()
            except OSError:
                continue
            unique[path] = SubtitleCandidate(path=path, source="lazyedit-data", mtime=stat.st_mtime)

        item.subtitles = sorted(unique.values(), key=lambda s: subtitle_priority(s.path))[:3]


def ffprobe_info(path: Path) -> tuple[float | None, int | None, int | None]:
    result = run([
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(path),
    ])
    if result.returncode != 0:
        return None, None, None
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None, None, None
    duration = None
    width = None
    height = None
    try:
        duration = float(data.get("format", {}).get("duration"))
    except (TypeError, ValueError):
        pass
    streams = data.get("streams") or []
    if streams:
        width = streams[0].get("width")
        height = streams[0].get("height")
    return duration, width, height


def link_or_copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        try:
            if dst.stat().st_size == src.stat().st_size:
                return
        except OSError:
            pass
        dst.unlink()
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def copy_text_sidecar(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    shutil.copy2(src, dst)
    if dst.suffix.lower() == ".srt":
        text = dst.read_text(encoding="utf-8", errors="replace")
        text = re.sub(r"[ \t]+$", "", text, flags=re.MULTILINE)
        text = re.sub(r"(\n\s*)+\Z", "\n", text)
        dst.write_text(text, encoding="utf-8")


def make_thumbnail(video: Path, thumb: Path, duration: float | None) -> None:
    if thumb.exists():
        return
    thumb.parent.mkdir(parents=True, exist_ok=True)
    ss = 1.5
    if duration and duration > 4:
        ss = min(duration / 3, 4)
    result = run([
        "ffmpeg",
        "-y",
        "-ss",
        f"{ss:.2f}",
        "-i",
        str(video),
        "-frames:v",
        "1",
        "-vf",
        "scale=640:-1",
        str(thumb),
    ])
    if result.returncode != 0 and thumb.exists():
        thumb.unlink()


def parse_srt(text: str) -> list[dict[str, str]]:
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return []
    blocks = re.split(r"\n\s*\n", text)
    entries: list[dict[str, str]] = []
    time_re = re.compile(r"(?P<start>\d{2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*(?P<end>\d{2}:\d{2}:\d{2}[,.]\d{3})")
    for block in blocks:
        lines = block.splitlines()
        time_idx = None
        match = None
        for i, line in enumerate(lines[:3]):
            match = time_re.search(line)
            if match:
                time_idx = i
                break
        if time_idx is None or match is None:
            continue
        body = "\n".join(lines[time_idx + 1 :]).strip()
        if body:
            entries.append({"start": match.group("start").replace(",", "."), "end": match.group("end").replace(",", "."), "text": body})
    return entries


ALLOWED_TAG_RE = re.compile(r"</?(?:ruby|rt|rp|br|b|i|em|strong)(?:\s*/?)>|<span(?:\s+class=\"[a-zA-Z0-9_ -]+\")?>|</span>")


def normalize_timestamp(value: str | None) -> str | None:
    if not value:
        return None
    text = str(value).strip().replace(",", ".")
    match = re.search(r"(\d{2}:\d{2}:\d{2}\.\d{1,3})", text)
    if not match:
        return None
    head, frac = match.group(1).split(".")
    return f"{head}.{frac[:3].ljust(3, '0')}"


def timestamp_to_seconds(value: str | None) -> float | None:
    timestamp = normalize_timestamp(value)
    if not timestamp:
        return None
    hh, mm, rest = timestamp.split(":")
    ss, ms = rest.split(".")
    return int(hh) * 3600 + int(mm) * 60 + int(ss) + int(ms) / 1000


def safe_subtitle_html(text: str) -> str:
    placeholders: list[str] = []

    def keep(match: re.Match[str]) -> str:
        placeholders.append(match.group(0))
        return f"@@TAG{len(placeholders) - 1}@@"

    protected = ALLOWED_TAG_RE.sub(keep, text)
    escaped = html.escape(protected)
    for idx, tag in enumerate(placeholders):
        escaped = escaped.replace(f"@@TAG{idx}@@", tag)
    return escaped.replace("\n", "<br>")


def token_class(part: str, lang: str | None = None) -> str:
    if re.fullmatch(r"[A-Za-z]+(?:'[A-Za-z]+)?", part):
        return "tok-en"
    if re.fullmatch(r"\d+(?:[.:]\d+)?", part):
        return "tok-num"
    if re.fullmatch(r"[\u3040-\u30ffー]+", part):
        return "tok-kana"
    if re.fullmatch(r"[\u4e00-\u9fff]+", part):
        return "tok-zh" if (lang or "").startswith("zh") else "tok-kanji"
    return "tok-punc"


def colorize_text(text: str, lang: str | None = None) -> str:
    parts = re.findall(r"\s+|[A-Za-z]+(?:'[A-Za-z]+)?|\d+(?:[.:]\d+)?|[\u3040-\u30ffー]+|[\u4e00-\u9fff]+|.", text)
    html_parts: list[str] = []
    for part in parts:
        escaped = html.escape(part)
        if part.isspace():
            html_parts.append(escaped)
        else:
            html_parts.append(f'<span class="tok {token_class(part, lang)}">{escaped}</span>')
    return "".join(html_parts)


def pair_values(pair: Any) -> tuple[str | None, str | None]:
    if isinstance(pair, dict):
        base = pair.get("base") or pair.get("text") or pair.get("word") or pair.get("kanji") or pair.get("surface")
        reading = pair.get("reading") or pair.get("furigana") or pair.get("kana") or pair.get("rt")
        return (str(base), str(reading)) if base and reading else (None, None)
    if isinstance(pair, (list, tuple)) and len(pair) >= 2 and pair[0] and pair[1]:
        return str(pair[0]), str(pair[1])
    return None, None


def ruby_html_from_pairs(text: str, pairs: Any) -> str | None:
    if not isinstance(pairs, list) or not pairs:
        return None
    rendered = html.escape(text)
    used = False
    for pair in pairs:
        base, reading = pair_values(pair)
        if not base or not reading:
            continue
        escaped_base = html.escape(base)
        ruby = f"<ruby>{escaped_base}<rt>{html.escape(reading)}</rt></ruby>"
        if escaped_base in rendered:
            rendered = rendered.replace(escaped_base, ruby, 1)
            used = True
    return rendered if used else None


def track_text_from_row(row: dict[str, Any], lang: str) -> str:
    if lang == "ja":
        return str(row.get("ruby") or row.get("ja") or row.get("text") or "").strip()
    if lang == "en":
        return str(row.get("en") or row.get("text") or "").strip()
    if lang == "zh":
        return str(row.get("zh") or row.get("zh_hant") or row.get("zh-Hant") or row.get("text") or "").strip()
    return str(row.get("text") or "").strip()


def track_html_from_row(row: dict[str, Any], lang: str) -> str:
    text = track_text_from_row(row, lang)
    if not text:
        return ""
    ruby = ruby_html_from_pairs(str(row.get("ja") or text), row.get("furigana_pairs"))
    if lang == "ja" and ruby:
        return ruby
    if "<" in text and ">" in text:
        return safe_subtitle_html(text)
    return colorize_text(text, lang)


def infer_rich_text_lang(path: Path, rows: list[dict[str, Any]]) -> str | None:
    lower = str(path).lower()
    sample = rows[0] if rows else {}
    if "/translations/en/" in lower or "_en." in lower or "en" in sample:
        return "en"
    if "/translations/zh" in lower or "_zh" in lower or "zh" in sample or "zh_hant" in sample:
        return "zh"
    if "/translations/ja/" in lower or "furigana" in lower or "ja" in sample or "ruby" in sample:
        return "ja"
    return None


_RICH_TEXT_JSON_FILES: list[Path] | None = None


def rich_text_json_files() -> list[Path]:
    global _RICH_TEXT_JSON_FILES
    if _RICH_TEXT_JSON_FILES is not None:
        return _RICH_TEXT_JSON_FILES
    files: list[Path] = []
    if LAZYEDIT_DATA_ROOT.exists():
        for path in walk_files(LAZYEDIT_DATA_ROOT):
            lower = str(path).lower()
            if path.suffix.lower() != ".json":
                continue
            if "/translations/" not in lower and "/burn/" not in lower:
                continue
            if any(token in lower for token in ("_en", "_zh", "_ja", "furigana", "translations")):
                files.append(path)
    _RICH_TEXT_JSON_FILES = files
    return files


def load_rich_rows(path: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        rows = data.get("lines") or data.get("entries") or data.get("segments") or []
    else:
        return []
    return [row for row in rows if isinstance(row, dict) and normalize_timestamp(row.get("start")) and normalize_timestamp(row.get("end"))]


def rich_timing_ratio(base_entries: list[dict[str, str]], rows: list[dict[str, Any]]) -> float:
    if not base_entries or not rows:
        return 0.0
    base_starts = [timestamp_to_seconds(entry.get("start")) for entry in base_entries]
    matched = 0
    for row in rows:
        start = timestamp_to_seconds(row.get("start"))
        if start is None:
            continue
        if any(base is not None and abs(base - start) <= 0.35 for base in base_starts):
            matched += 1
    return matched / max(1, min(len(base_entries), len(rows)))


def rich_text_score(item: MediaItem, path: Path) -> int:
    tokens = stem_tokens(item.canonical.path)
    path_tokens = stem_tokens(path)
    shared = tokens & path_tokens
    return len(shared) + sum(2 for token in shared if len(token) >= 7)


def load_rich_text_tracks(item: MediaItem, base_entries: list[dict[str, str]]) -> dict[str, list[dict[str, Any]]]:
    selected: dict[str, tuple[float, int, Path, list[dict[str, Any]]]] = {}
    for path in rich_text_json_files():
        score = rich_text_score(item, path)
        if score < 3:
            continue
        rows = load_rich_rows(path)
        if not rows:
            continue
        ratio = rich_timing_ratio(base_entries, rows)
        if ratio < 0.6:
            continue
        lang = infer_rich_text_lang(path, rows)
        if not lang:
            continue
        has_furigana = any(row.get("furigana_pairs") for row in rows)
        rank = ratio * 100 + score * 10 + (8 if has_furigana else 0)
        current = selected.get(lang)
        if current is None or rank > current[0]:
            selected[lang] = (rank, score, path, rows)
    return {lang: rows for lang, (_, _, _, rows) in selected.items()}


def match_rich_row(entry: dict[str, str], rows: list[dict[str, Any]], fallback_index: int) -> dict[str, Any] | None:
    start = timestamp_to_seconds(entry.get("start"))
    if start is not None:
        best: tuple[float, dict[str, Any]] | None = None
        for row in rows:
            row_start = timestamp_to_seconds(row.get("start"))
            if row_start is None:
                continue
            diff = abs(row_start - start)
            if diff <= 0.35 and (best is None or diff < best[0]):
                best = (diff, row)
        if best:
            return best[1]
    if fallback_index < len(rows):
        row = rows[fallback_index]
        row_start = timestamp_to_seconds(row.get("start"))
        if start is None or row_start is None or abs(row_start - start) <= 0.5:
            return row
    return None


def enrich_transcript_entries(item: MediaItem, entries: list[dict[str, str]]) -> list[dict[str, Any]]:
    tracks = load_rich_text_tracks(item, entries)
    enriched: list[dict[str, Any]] = []
    for idx, entry in enumerate(entries):
        line_tracks: dict[str, dict[str, str]] = {}
        for lang, rows in tracks.items():
            row = match_rich_row(entry, rows, idx)
            if not row:
                continue
            text = track_text_from_row(row, lang)
            if not text:
                continue
            label = {"ja": "日本語", "en": "English", "zh": "中文"}.get(lang, lang.upper())
            line_tracks[lang] = {
                "label": label,
                "text": text,
                "html": track_html_from_row(row, lang),
            }
        start = normalize_timestamp(entry.get("start")) or entry.get("start", "")
        end = normalize_timestamp(entry.get("end")) or entry.get("end", "")
        enriched.append(
            {
                "start": start,
                "end": end,
                "start_seconds": timestamp_to_seconds(start),
                "end_seconds": timestamp_to_seconds(end),
                "text": entry.get("text", ""),
                "html": colorize_text(entry.get("text", "")),
                "tracks": line_tracks,
            }
        )
    return enriched


def copy_subtitles_and_transcript(item: MediaItem) -> None:
    if not item.subtitles:
        return
    sub_dir = REPO_ROOT / "media" / "subtitles" / item.slug
    sub_dir.mkdir(parents=True, exist_ok=True)
    selected_srt: Path | None = None
    copied: list[str] = []
    for idx, sub in enumerate(item.subtitles, 1):
        suffix = sub.path.suffix.lower()
        dst = sub_dir / f"subtitle-{idx}{suffix}"
        copy_text_sidecar(sub.path, dst)
        copied.append(str(dst.relative_to(REPO_ROOT)))
        if selected_srt is None and sub.path.suffix.lower() == ".srt":
            selected_srt = dst
    item.subtitle_rel_files = copied
    if selected_srt:
        entries = parse_srt(selected_srt.read_text(encoding="utf-8", errors="replace"))
        enriched_entries = enrich_transcript_entries(item, entries)
        transcript = {
            "slug": item.slug,
            "source_subtitle": str(selected_srt.relative_to(REPO_ROOT)),
            "subtitle_files": copied,
            "entries": enriched_entries,
        }
        out = REPO_ROOT / "data" / "transcripts" / f"{item.slug}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(transcript, ensure_ascii=False, indent=2), encoding="utf-8")
        item.transcript_rel = str(out.relative_to(REPO_ROOT))


def process_media(items: list[MediaItem], dry_run: bool) -> None:
    for idx, item in enumerate(items, 1):
        ext = item.canonical.path.suffix.lower()
        video_dst = REPO_ROOT / "media" / "videos" / f"{item.slug}{ext}"
        thumb_dst = REPO_ROOT / "media" / "thumbs" / f"{item.slug}.jpg"
        item.video_rel = str(video_dst.relative_to(REPO_ROOT))
        item.thumb_rel = str(thumb_dst.relative_to(REPO_ROOT))
        item.page_rel = f"videos/{item.slug}.html"
        if dry_run:
            continue
        print(f"[{idx}/{len(items)}] {item.slug}", file=sys.stderr)
        link_or_copy(item.canonical.path, video_dst)
        item.duration, item.width, item.height = ffprobe_info(video_dst)
        make_thumbnail(video_dst, thumb_dst, item.duration)
        copy_subtitles_and_transcript(item)


def human_size(num: int) -> str:
    value = float(num)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{num} B"


def read_transcript_data(item: MediaItem) -> dict[str, Any]:
    if not item.transcript_rel:
        return {}
    path = REPO_ROOT / item.transcript_rel
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def json_for_script(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False).replace("</", "<\\/")


def render_page(item: MediaItem) -> str:
    transcript = read_transcript_data(item)
    entries = transcript.get("entries") or []
    transcript_json = json_for_script(transcript if entries else {"slug": item.slug, "entries": []})
    caption_hint = (
        "Play the video to see the current timed line. Japanese, English, and Chinese tracks are shown when LazyEdit provides them."
        if entries
        else "No LazyEdit timed subtitle sidecar was matched for this video yet."
    )

    if VIDEO_BASE_URL:
        video_src = f"{VIDEO_BASE_URL}/{Path(item.video_rel).name}"
    else:
        video_src = f"../{item.video_rel}"

    sources_html = "\n".join(
        f"<li><code>{html.escape(source.path.name)}</code> <span>{html.escape(source.source)}</span></li>" for source in item.sources[:8]
    )
    subtitle_html = "\n".join(
        f"<li><code>{html.escape(Path(rel).name)}</code> <span>text subtitle sidecar</span></li>" for rel in item.subtitle_rel_files
    ) or "<li>No subtitle sidecar found.</li>"
    duration = f"{item.duration:.1f}s" if item.duration else "unknown"
    dims = f"{item.width}x{item.height}" if item.width and item.height else "unknown"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(item.title)} - LalaMedias</title>
  <meta name="description" content="{html.escape(item.description)}">
  <link rel="stylesheet" href="../assets/style.css">
</head>
<body>
  <header class="topbar">
    <a href="../index.html">LalaMedias</a>
    <span>{html.escape(item.title)}</span>
  </header>
  <main class="video-page">
    <section class="hero">
      <video controls preload="metadata" poster="../{html.escape(item.thumb_rel)}" src="{html.escape(video_src)}"></video>
      <section class="caption-stage" aria-label="Timed multilingual subtitles">
        <div class="current-caption" id="current-caption" aria-live="polite">
          <p class="caption-empty">{html.escape(caption_hint)}</p>
        </div>
      </section>
      <div class="meta">
        <h1>{html.escape(item.title)}</h1>
        <p>{html.escape(item.description)}</p>
        <p>{html.escape(duration)} · {html.escape(dims)} · {human_size(item.canonical.size)} · category <code>{html.escape(item.publish_category)}</code> · SHA-256 <code>{item.sha256[:12]}</code></p>
      </div>
    </section>
    <details>
      <summary>Source Files</summary>
      <ul>{sources_html}</ul>
    </details>
    <details>
      <summary>Matched Subtitle Files</summary>
      <ul>{subtitle_html}</ul>
    </details>
  </main>
  <script id="timed-text-data" type="application/json">{transcript_json}</script>
  <script>
    (() => {{
      const video = document.querySelector("video");
      const panel = document.getElementById("current-caption");
      const dataEl = document.getElementById("timed-text-data");
      if (!video || !panel || !dataEl) return;
      let data = {{}};
      try {{ data = JSON.parse(dataEl.textContent || "{{}}"); }} catch (_) {{ data = {{ entries: [] }}; }}
      const entries = Array.isArray(data.entries) ? data.entries : [];
      const order = [
        ["ja", "JP"],
        ["en", "EN"],
        ["zh", "ZH"],
      ];
      let currentIndex = -2;

      function lineForTime(time) {{
        for (let i = 0; i < entries.length; i++) {{
          const entry = entries[i];
          const start = Number(entry.start_seconds);
          const end = Number(entry.end_seconds);
          if (Number.isFinite(start) && Number.isFinite(end) && time >= start && time < end) return i;
        }}
        return -1;
      }}

      function renderEmpty(message) {{
        panel.innerHTML = `<p class="caption-empty">${{message}}</p>`;
      }}

      function renderEntry(entry) {{
        const rows = [];
        const tracks = entry.tracks || {{}};
        for (const [lang, shortLabel] of order) {{
          if (!tracks[lang]) continue;
          rows.push(`<div class="caption-row caption-${{lang}}"><span class="caption-label">${{shortLabel}}</span><div class="caption-text">${{tracks[lang].html || tracks[lang].text || ""}}</div></div>`);
        }}
        if (!rows.length && (entry.html || entry.text)) {{
          rows.push(`<div class="caption-row caption-source"><span class="caption-label">LINE</span><div class="caption-text">${{entry.html || entry.text}}</div></div>`);
        }}
        if (!rows.length) {{
          renderEmpty("No timed text for this moment.");
          return;
        }}
        const time = `${{entry.start || ""}} - ${{entry.end || ""}}`;
        panel.innerHTML = `<div class="caption-time">${{time}}</div>${{rows.join("")}}`;
      }}

      function updateCaption() {{
        if (!entries.length) return;
        const idx = lineForTime(video.currentTime || 0);
        if (idx === currentIndex) return;
        currentIndex = idx;
        if (idx < 0) renderEmpty(" ");
        else renderEntry(entries[idx]);
      }}

      video.addEventListener("timeupdate", updateCaption);
      video.addEventListener("seeked", updateCaption);
      video.addEventListener("play", updateCaption);
      video.addEventListener("loadedmetadata", updateCaption);
    }})();
  </script>
</body>
</html>
"""


def render_index(items: list[MediaItem]) -> str:
    cards = []
    for item in items:
        duration = f"{item.duration:.0f}s" if item.duration else ""
        transcript = " transcript" if item.transcript_rel else ""
        cards.append(
            f"""<a class="card" href="{html.escape(item.page_rel)}">
  <img loading="lazy" src="{html.escape(item.thumb_rel)}" alt="">
  <strong>{html.escape(item.title)}</strong>
  <span>{html.escape(duration)} · {html.escape(item.publish_category)}{html.escape(transcript)}</span>
  <small>{html.escape(item.description)}</small>
</a>"""
        )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>LalaMedias</title>
  <link rel="stylesheet" href="assets/style.css">
</head>
<body>
  <header class="site-hero">
    <p>LALACHAN Media Archive</p>
    <h1>Generated videos, stories, and subtitles</h1>
    <p class="lede">A deduplicated archive of generated LALACHAN videos. Each page uses a clean source video from LALACHAN/Videos and renders only the current timed subtitle line below the player when matched text tracks are available.</p>
  </header>
  <main>
    <section class="stats">
      <span>{len(items)} videos</span>
      <span>{sum(1 for item in items if item.transcript_rel)} with matched transcripts</span>
      <span>{human_size(sum(item.canonical.size for item in items))} canonical video data</span>
    </section>
    <section class="grid">
      {''.join(cards)}
    </section>
  </main>
</body>
</html>
"""


def render_css() -> str:
    return """* { box-sizing: border-box; }
:root {
  color-scheme: light;
  --ink: #15151a;
  --muted: #686875;
  --line: #e5e5eb;
  --paper: #fbfbfd;
  --panel: #ffffff;
  --accent: #2b63ff;
}
body {
  margin: 0;
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  background: var(--paper);
  color: var(--ink);
}
a { color: inherit; text-decoration: none; }
code { font-size: 0.9em; overflow-wrap: anywhere; }
.site-hero {
  padding: 48px clamp(18px, 5vw, 72px) 28px;
  background: linear-gradient(135deg, #ffffff, #eef4ff);
  border-bottom: 1px solid var(--line);
}
.site-hero p:first-child {
  margin: 0 0 8px;
  color: var(--accent);
  font-weight: 700;
  letter-spacing: 0;
}
h1 {
  margin: 0;
  font-size: clamp(2rem, 5vw, 4.4rem);
  line-height: 1.02;
  letter-spacing: 0;
}
h2 {
  margin-top: 34px;
  letter-spacing: 0;
}
.lede {
  max-width: 780px;
  color: var(--muted);
  font-size: 1.08rem;
  line-height: 1.65;
}
main {
  width: min(1180px, calc(100vw - 32px));
  margin: 0 auto;
}
.stats {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  padding: 20px 0;
}
.stats span {
  border: 1px solid var(--line);
  background: var(--panel);
  padding: 8px 12px;
  border-radius: 999px;
  color: var(--muted);
}
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(230px, 1fr));
  gap: 18px;
  padding-bottom: 56px;
}
.card {
  display: grid;
  gap: 10px;
  border: 1px solid var(--line);
  background: var(--panel);
  border-radius: 8px;
  padding: 10px;
}
.card img {
  width: 100%;
  aspect-ratio: 16 / 9;
  object-fit: cover;
  background: #ececf1;
  border-radius: 6px;
}
.card strong {
  line-height: 1.25;
}
.card span,
.card small,
.hint,
.empty {
  color: var(--muted);
}
.card small {
  line-height: 1.45;
}
.topbar {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  padding: 14px clamp(16px, 4vw, 40px);
  border-bottom: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.92);
  position: sticky;
  top: 0;
  backdrop-filter: blur(12px);
  z-index: 2;
}
.topbar a { color: var(--accent); font-weight: 700; }
.video-page {
  max-width: 960px;
  padding-bottom: 72px;
}
.hero {
  padding-top: 24px;
}
video {
  width: 100%;
  max-height: 78vh;
  background: #111;
  border-radius: 8px;
  display: block;
}
.meta p {
  color: var(--muted);
}
.caption-stage {
  margin-top: 14px;
}
.current-caption {
  border: 1px solid var(--line);
  background: var(--panel);
  border-radius: 8px;
  padding: clamp(14px, 3vw, 22px);
  min-height: 142px;
  box-shadow: 0 18px 48px rgba(24, 37, 68, 0.08);
}
.caption-empty {
  color: var(--muted);
  margin: 0;
}
.caption-time {
  color: var(--muted);
  font-variant-numeric: tabular-nums;
  font-size: 0.85rem;
  margin-bottom: 10px;
}
.caption-row {
  display: grid;
  grid-template-columns: 48px minmax(0, 1fr);
  gap: 12px;
  align-items: baseline;
  margin: 9px 0;
}
.caption-label {
  color: #fff;
  background: #15151a;
  border-radius: 999px;
  padding: 3px 8px;
  font-size: 0.72rem;
  font-weight: 800;
  letter-spacing: 0;
  text-align: center;
}
.caption-ja .caption-label { background: #6f5cff; }
.caption-en .caption-label { background: #117c67; }
.caption-zh .caption-label { background: #d14f2f; }
.caption-source .caption-label { background: #434350; }
.caption-text {
  font-size: clamp(1.15rem, 2.2vw, 1.72rem);
  line-height: 1.72;
  font-weight: 680;
  letter-spacing: 0;
}
.tok {
  display: inline-block;
  margin: 0 0.01em;
}
.tok-kanji {
  color: #3757d6;
}
.tok-kana {
  color: #6f5cff;
}
.tok-zh {
  color: #d14f2f;
}
.tok-en {
  color: #117c67;
}
.tok-num {
  color: #986400;
}
.tok-punc {
  color: var(--muted);
}
ruby rt {
  font-size: 0.52em;
  font-weight: 700;
  color: #6f5cff;
}
details {
  margin-top: 18px;
  border: 1px solid var(--line);
  background: var(--panel);
  border-radius: 8px;
  padding: 12px 14px;
}
details li {
  margin: 8px 0;
}
@media (max-width: 700px) {
  .caption-row {
    grid-template-columns: 1fr;
    gap: 6px;
  }
  .topbar {
    display: block;
  }
}
"""


def write_site(items: list[MediaItem]) -> None:
    (REPO_ROOT / "assets").mkdir(exist_ok=True)
    (REPO_ROOT / "videos").mkdir(exist_ok=True)
    (REPO_ROOT / "data").mkdir(exist_ok=True)
    (REPO_ROOT / "assets" / "style.css").write_text(render_css(), encoding="utf-8")
    (REPO_ROOT / "index.html").write_text(render_index(items), encoding="utf-8")
    for item in items:
        (REPO_ROOT / item.page_rel).write_text(render_page(item), encoding="utf-8")
    manifest = []
    for item in items:
        manifest.append(
            {
                "slug": item.slug,
                "title": item.title,
                "description": item.description,
                "publish_category": item.publish_category,
                "sha256": item.sha256,
                "video": item.video_rel,
                "video_url": f"{VIDEO_BASE_URL}/{Path(item.video_rel).name}" if VIDEO_BASE_URL else item.video_rel,
                "thumbnail": item.thumb_rel if (REPO_ROOT / item.thumb_rel).exists() else None,
                "page": item.page_rel,
                "duration": item.duration,
                "width": item.width,
                "height": item.height,
                "size": item.canonical.size,
                "transcript": item.transcript_rel,
                "subtitle_files": item.subtitle_rel_files,
                "sources": [
                    {
                        "source": s.source,
                        "name": s.path.name,
                        "size": s.size,
                        "mtime": dt.datetime.fromtimestamp(s.mtime).isoformat(),
                    }
                    for s in item.sources
                ],
            }
        )
    (REPO_ROOT / "data" / "videos.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {
        "generated_at": dt.datetime.now().isoformat(),
        "video_count": len(items),
        "with_transcripts": sum(1 for item in items if item.transcript_rel),
        "canonical_video_bytes": sum(item.canonical.size for item in items),
        "source_policy": "videos are collected only from the top-level LALACHAN/Videos folder; text subtitles may be matched from LazyEdit sidecars",
    }
    (REPO_ROOT / "data" / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


def write_readme(items: list[MediaItem]) -> None:
    lines = [
        "# LalaMedias",
        "",
        "A Git LFS media archive and static website for generated LALACHAN videos.",
        "",
        "The archive collects only clean generated videos from the top-level `LALACHAN/Videos` folder. LazyEdit is used only as a source for timed text sidecars and multilingual translation tracks. Downloads, LazyEdit rendered MP4s, personal phone videos, and burned-subtitle/logo/portrait variants are intentionally excluded.",
        "",
        "## Browse Locally",
        "",
        "```bash",
        "python3 -m http.server 8080",
        "```",
        "",
        "Then open `http://127.0.0.1:8080/`.",
        "",
        "## Refresh The Archive",
        "",
        "```bash",
        "python3 scripts/collect_lala_medias.py",
        "```",
        "",
        "The generator uses these source classes:",
        "",
        "- videos: `lalachan-videos` only",
        "- subtitles: `lazyedit-data` text sidecars only",
        "",
        "Override local scan roots with `LALACHAN_ROOT` and `LAZYEDIT_DATA_ROOT`.",
        "",
        "LazyEdit subtitle matching prefers `*_mixed_polished.srt`, then polished, mixed, and caption files. When timed `ja`, `en`, or `zh` translation JSON exists, pages render only the current active line below the video with ruby-preserving markup and word coloring.",
        "",
        "Titles, descriptions, and publish categories are viewer-facing metadata, following the same concise style used for LazyEdit submission. Full scripts are not copied into metadata.",
        "",
        "## Current Contents",
        "",
        f"- Videos: `{len(items)}`",
        f"- Videos with matched transcript sidecars: `{sum(1 for item in items if item.transcript_rel)}`",
        f"- Canonical video data: `{human_size(sum(item.canonical.size for item in items))}`",
        "",
        "## Storage Note",
        "",
        "The website, thumbnails, subtitles, and manifests live in Git. MP4 files are kept locally under `media/videos/` and can be uploaded as GitHub Release assets. Set `LALAMEDIAS_VIDEO_BASE_URL` before regenerating the site to make public pages point at release-hosted MP4 files.",
    ]
    (REPO_ROOT / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def clean_generated_outputs() -> None:
    for rel in [
        "media/videos",
        "media/thumbs",
        "media/subtitles",
        "data/transcripts",
        "videos",
    ]:
        shutil.rmtree(REPO_ROOT / rel, ignore_errors=True)
    for rel in [
        "data/videos.json",
        "data/summary.json",
        "index.html",
        "assets/style.css",
    ]:
        path = REPO_ROOT / rel
        if path.exists():
            path.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect LALACHAN generated videos into LalaMedias.")
    parser.add_argument("--dry-run", action="store_true", help="Only scan and report; do not copy media or write the site.")
    parser.add_argument("--clean", action="store_true", help="Remove previously generated archive media/pages before rebuilding.")
    args = parser.parse_args()

    videos, subtitles = collect_candidates()
    print(f"candidate videos: {len(videos)}", file=sys.stderr)
    print(f"candidate lazyedit subtitles: {len(subtitles)}", file=sys.stderr)

    lazy_videos = [v for v in videos if v.source == "lazyedit-data"]
    items = group_by_hash(videos)
    attach_subtitles(items, lazy_videos, subtitles)

    print(f"unique videos: {len(items)}", file=sys.stderr)
    print(f"canonical bytes: {human_size(sum(item.canonical.size for item in items))}", file=sys.stderr)
    print(f"subtitle matched: {sum(1 for item in items if item.subtitles)}", file=sys.stderr)

    if args.dry_run:
        process_media(items, dry_run=True)
        return 0

    if args.clean:
        clean_generated_outputs()
    process_media(items, dry_run=False)
    write_site(items)
    write_readme(items)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
