# LalaMedias

A Git LFS media archive and static website for generated LALACHAN videos.

The archive collects only clean generated videos from the top-level `LALACHAN/Videos` folder. LazyEdit is used only as a source for text subtitle sidecars. Downloads, LazyEdit rendered MP4s, personal phone videos, and burned-subtitle/logo/portrait variants are intentionally excluded.

## Browse Locally

```bash
python3 -m http.server 8080
```

Then open `http://127.0.0.1:8080/`.

## Refresh The Archive

```bash
python3 scripts/collect_lala_medias.py
```

The generator uses these source classes:

- videos: `lalachan-videos` only
- subtitles: `lazyedit-data` text sidecars only

Override local scan roots with `LALACHAN_ROOT` and `LAZYEDIT_DATA_ROOT`.

LazyEdit subtitle matching prefers `*_mixed_polished.srt`, then polished, mixed, and caption files.

Titles, descriptions, and publish categories are viewer-facing metadata, following the same concise style used for LazyEdit submission. Full scripts are not copied into metadata.

## Current Contents

- Videos: `46`
- Videos with matched transcript sidecars: `43`
- Canonical video data: `1.1 GB`

## Storage Note

The website, thumbnails, subtitles, and manifests live in Git. MP4 files are kept locally under `media/videos/` and can be uploaded as GitHub Release assets. Set `LALAMEDIAS_VIDEO_BASE_URL` before regenerating the site to make public pages point at release-hosted MP4 files.
