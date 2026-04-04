"""
Knowledge base loader for AI message generation.

Loads and samples entries from video_transcripts.csv stored in this directory.
The file is parsed once and cached in-process.

CSV columns (with header row):
  Video Name | Transcript | Summary | Source | Category | PublicVideoLink

Usage:
    from app.prompts.knowledge_base import load_video_transcripts

    stories = load_video_transcripts(sample_size=5)
    # Returns a string of sampled story entries ready for prompt injection.
"""
from __future__ import annotations

import csv
import io
import logging
import random
import re
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

_KB_DIR = Path(__file__).resolve().parent

EXPECTED_PATH = _KB_DIR / "video_transcripts.csv"

# Column indices (0-based) matching the CSV header
_COL_TITLE    = 0
_COL_SUMMARY  = 2
_COL_PLATFORM = 3
_COL_CATEGORY = 4
_COL_URL      = 5

_HEADER_TITLES = {"video name", "title", "name", "video title", "story title"}


@lru_cache(maxsize=1)
def _load_stories() -> tuple[dict, ...]:
    """
    Parse video_transcripts.csv and return all story rows as an immutable tuple
    of dicts. Cached after first read.

    Each dict has keys: title, summary, platform, category, url, block
    where `block` is the pre-formatted string for prompt injection.
    """
    if not EXPECTED_PATH.exists():
        logger.warning(
            "knowledge_base: %s not found — story injection will be skipped",
            EXPECTED_PATH,
        )
        return ()

    try:
        raw = EXPECTED_PATH.read_text(encoding="utf-8-sig")  # utf-8-sig strips BOM if present
    except Exception:
        logger.error("knowledge_base: failed to read %s", EXPECTED_PATH, exc_info=True)
        return ()

    stories: list[dict] = []
    reader = csv.reader(io.StringIO(raw))
    for row in reader:
        if not row:
            continue

        title = row[_COL_TITLE].strip() if len(row) > _COL_TITLE else ""

        # Skip blank rows and the header row
        if not title or title.lower() in _HEADER_TITLES:
            continue

        summary  = row[_COL_SUMMARY].strip()  if len(row) > _COL_SUMMARY  else ""
        platform = row[_COL_PLATFORM].strip() if len(row) > _COL_PLATFORM else ""
        category = row[_COL_CATEGORY].strip() if len(row) > _COL_CATEGORY else ""
        url      = row[_COL_URL].strip()      if len(row) > _COL_URL      else ""

        lines = [f"Title: {title}"]
        if platform or category:
            lines.append(f"Platform: {' | '.join(filter(None, [platform, category]))}")
        if summary:
            lines.append(f'Summary: "{summary}"')
        if url:
            lines.append(f"URL: {url}")

        stories.append({
            "title":    title,
            "summary":  summary,
            "platform": platform,
            "category": category,
            "url":      url,
            "block":    "\n".join(lines),
        })

    logger.info("knowledge_base: loaded %d stories from %s", len(stories), EXPECTED_PATH)
    return tuple(stories)


def load_video_transcripts(sample_size: int = 5, max_chars_per_story: int = 600) -> str:
    """
    Return a random sample of video transcript stories as a formatted string
    ready for injection into a prompt.

    sample_size: number of stories to include (default 5).
    max_chars_per_story: truncate long story blocks to keep total prompt size manageable.
      The Summary field is already short, so truncation rarely fires in practice.

    Returns empty string if the file is missing or unreadable — prompt
    generation degrades gracefully (story section is omitted).
    """
    stories = _load_stories()
    if not stories:
        return ""

    # Prefer stories whose summary mentions duration cues ("3 min", "under 5 min")
    # so the AI selects stories that feel like short, watchable videos.
    short_stories = [
        s for s in stories
        if re.search(r"\b[1-4]\s*min|\bunder\s+5\s+min|< ?5\s*min", s["summary"], re.IGNORECASE)
    ]
    pool = short_stories if len(short_stories) >= 3 else list(stories)

    sampled = random.sample(pool, min(sample_size, len(pool)))

    lines = ["=== STUDENT SUCCESS STORIES (knowledge base) ===\n"]
    for i, story in enumerate(sampled, 1):
        block = story["block"]
        if len(block) > max_chars_per_story:
            block = block[:max_chars_per_story].rsplit(" ", 1)[0] + "..."
        lines.append(f"[Story {i}]\n{block}\n")

    return "\n".join(lines)
