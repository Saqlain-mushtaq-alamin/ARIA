"""Natural-language task parsing.

This module converts messy user text into structured task objects the scheduler
can reason about.

Primary entry point: `parse_tasks(text)`.

Output format (list of dicts) is intentionally simple and JSON-friendly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from typing import Any, Iterable

import spacy


_TIME_AMPM_RE = re.compile(
    r"\b(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<ampm>a\.?m\.?|p\.?m\.?)\b",
    re.IGNORECASE,
)

_TIME_COLON_RE = re.compile(
    r"\b(?P<hour>\d{1,2}):(?P<minute>\d{2})\b",
    re.IGNORECASE,
)

_TIME_WORD_RE = re.compile(r"\b(noon|midnight)\b", re.IGNORECASE)

# Common "glue" prefixes users add before tasks.
_PREFIX_RE = re.compile(
    r"^\s*(i\s+(have|got|need|need\s+to|must|have\s+to)|need\s+to|have\s+to|must|please|can\s+you|could\s+you|today\s+i\s+need\s+to)\s+",
    re.IGNORECASE,
)

# Leading verbs that typically aren't part of the task name.
_LEADING_VERB_RE = re.compile(
    r"^\s*(finish|complete|do|study|read|write|work\s+on|submit|prepare|attend|go\s+to)\s+",
    re.IGNORECASE,
)

# Splitting heuristic: commas / semicolons / "and" between items.
_SPLIT_RE = re.compile(r"\s*(?:,|;|\band\b|\bthen\b)\s*", re.IGNORECASE)


@dataclass(frozen=True)
class ParseConfig:
    """Heuristic configuration for parsing."""

    # Duration estimates when user gives "3 chapters" etc.
    minutes_per_chapter: int = 60
    minutes_per_page: int = 5

    # Default durations for common fixed tasks if missing.
    default_durations_minutes: dict[str, int] | None = None


_DEFAULT_DURATIONS: dict[str, int] = {
    "class": 120,
    "lecture": 60,
    "gym": 60,
    "meeting": 60,
    "appointment": 60,
    "work": 480,
}


@lru_cache(maxsize=1)
def _nlp():
    """Load spaCy model if available; fall back to a blank pipeline."""

    try:
        return spacy.load("en_core_web_sm")
    except Exception:
        return spacy.blank("en")


def _minutes_to_duration_str(minutes: int) -> str:
    if minutes <= 0:
        return "0m"
    if minutes % 60 == 0:
        return f"{minutes // 60}h"
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}h{mins}m"


def _parse_time_to_hhmm(text: str) -> str | None:
    """Parse a time expression into HH:MM (24h)."""

    # Word times.
    word = _TIME_WORD_RE.search(text)
    if word:
        w = word.group(1).lower()
        return "12:00" if w == "noon" else "00:00"

    # Prefer explicit am/pm formats.
    m = _TIME_AMPM_RE.search(text)
    if m:
        hour = int(m.group("hour"))
        minute = int(m.group("minute") or "0")
        ampm_raw = (m.group("ampm") or "").lower().replace(".", "")
        ampm = "am" if ampm_raw.startswith("a") else "pm"

        if minute < 0 or minute > 59:
            return None
        if hour < 1 or hour > 12:
            return None
        if ampm == "am":
            hour = 0 if hour == 12 else hour
        else:
            hour = 12 if hour == 12 else hour + 12
        return f"{hour:02d}:{minute:02d}"

    # 24-hour clock with colon.
    m = _TIME_COLON_RE.search(text)
    if not m:
        return None

    hour = int(m.group("hour"))
    minute = int(m.group("minute"))
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        return None
    return f"{hour:02d}:{minute:02d}"


def _extract_deadline(segment: str, reference_dt: datetime | None) -> str | None:
    lowered = segment.lower()
    if "today" in lowered:
        return "today"
    if "tomorrow" in lowered:
        return "tomorrow"
    if "tonight" in lowered:
        return "today"

    # If user expresses urgency but doesn't say when, default to today.
    if re.search(r"\b(need\s+to|have\s+to|must|due|finish|complete|submit)\b", lowered):
        return "today"

    # "by <time>" => treat as today if no explicit date.
    if re.search(r"\bby\b", lowered):
        # If weekday mentioned, use that.
        for weekday in (
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
            "friday",
            "saturday",
            "sunday",
        ):
            if weekday in lowered:
                return weekday

        # If the phrase contains a time but no date, assume today.
        if _parse_time_to_hhmm(segment) is not None:
            return "today"

    # spaCy DATE entities as a last pass (e.g. "by Monday").
    doc = _nlp()(segment)
    for ent in doc.ents:
        if ent.label_ == "DATE":
            text = ent.text.strip().lower()
            if text in {
                "today",
                "tomorrow",
                "tonight",
                "monday",
                "tuesday",
                "wednesday",
                "thursday",
                "friday",
                "saturday",
                "sunday",
            }:
                return "today" if text == "tonight" else text

    _ = reference_dt  # reserved for future normalization
    return None


def _extract_duration_minutes(segment: str, config: ParseConfig) -> int | None:
    s = segment.lower()

    # Explicit durations: "for 2 hours", "2h", "30 minutes".
    dur_match = re.search(
        r"\b(?P<num>\d+)\s*(?P<unit>h|hr|hrs|hour|hours|m|min|mins|minute|minutes)\b",
        s,
    )
    if dur_match:
        num = int(dur_match.group("num"))
        unit = dur_match.group("unit")
        if unit.startswith("h"):
            return num * 60
        return num

    # Quantities that imply effort (chapters/pages), allowing intervening words.
    qty_match = re.search(
        r"\b(?P<num>\d+)\b(?:\s+\w+){0,3}\s+(?P<unit>chapters?|pages?)\b",
        s,
    )
    if qty_match:
        num = int(qty_match.group("num"))
        unit = qty_match.group("unit")
        if unit.startswith("chapter"):
            return num * config.minutes_per_chapter
        return num * config.minutes_per_page

    # Assignments are often medium/heavy work; default to 2h per assignment.
    assign_match = re.search(
        r"\b(?P<num>\d+)\b(?:\s+\w+){0,2}\s+assignments?\b",
        s,
    )
    if assign_match:
        num = int(assign_match.group("num"))
        return num * 120

    return None


def _estimate_default_duration_minutes(task_name: str, config: ParseConfig) -> int | None:
    defaults = config.default_durations_minutes or _DEFAULT_DURATIONS
    name = task_name.strip().lower()

    # Direct match.
    if name in defaults:
        return defaults[name]

    # Keyword contains match.
    for key, minutes in defaults.items():
        if key in name:
            return minutes

    return None


def _clean_task_name(segment: str) -> str:
    s = segment.strip()

    # Remove common prefixes.
    s = _PREFIX_RE.sub("", s).strip()

    # Remove deadline phrases.
    s = re.sub(r"\bby\b\s+[^,;]+", "", s, flags=re.IGNORECASE).strip()

    # Remove time phrases: "at 9am", "around 6pm", "at 14:30", "at noon".
    s = re.sub(r"\b(at|around|on)\b\s*" + _TIME_AMPM_RE.pattern, "", s, flags=re.IGNORECASE).strip()
    s = re.sub(r"\b(at|around|on)\b\s*" + _TIME_COLON_RE.pattern, "", s, flags=re.IGNORECASE).strip()
    s = re.sub(r"\b(at|around|on)\b\s*" + _TIME_WORD_RE.pattern, "", s, flags=re.IGNORECASE).strip()
    s = _TIME_AMPM_RE.sub("", s).strip()
    s = _TIME_COLON_RE.sub("", s).strip()
    s = _TIME_WORD_RE.sub("", s).strip()

    # Remove explicit duration phrases: "for 2 hours", "2h".
    s = re.sub(
        r"\bfor\b\s+\d+\s*(h|hr|hrs|hour|hours|m|min|mins|minute|minutes)\b",
        "",
        s,
        flags=re.IGNORECASE,
    ).strip()

    # Remove leading verbs.
    s = _LEADING_VERB_RE.sub("", s).strip()

    # Cleanup dangling punctuation/extra words.
    s = re.sub(r"\s+", " ", s).strip(" -.:\t\n")

    # If the cleaned name starts with a workload quantity, drop the number.
    # Example: "3 chemistry chapters" -> "chemistry chapters".
    if re.match(r"^\d+\b", s) and re.search(r"\b(chapters?|pages?|assignments?)\b", s, flags=re.IGNORECASE):
        s = re.sub(r"^\d+\s+", "", s).strip()

    return s


def _segments(text: str) -> list[str]:
    # Avoid splitting inside very short expressions like "9am" by trimming first.
    raw = [p.strip() for p in _SPLIT_RE.split(text) if p.strip()]
    return [p for p in raw if len(p) > 0]


def parse_tasks(
    text: str,
    *,
    reference_dt: datetime | None = None,
    config: ParseConfig | None = None,
) -> list[dict[str, Any]]:
    """Parse free-form text into a list of structured tasks.

    Each task dict contains:
    - task: str
    - type: "fixed" | "flexible"
    - time: "HH:MM" (only for fixed tasks)
    - duration: e.g. "2h", "45m", "1h30m" (when inferred)
    - deadline: e.g. "today", "tomorrow", "monday" (when inferred)
    """

    cfg = config or ParseConfig()
    out: list[dict[str, Any]] = []

    for seg in _segments(text):
        if not seg:
            continue

        # Determine fixed time.
        time_hhmm = _parse_time_to_hhmm(seg)

        # Deadline.
        deadline = _extract_deadline(seg, reference_dt)

        # Task name.
        task_name = _clean_task_name(seg)
        if not task_name:
            # Fall back to noun chunks / content words.
            doc = _nlp()(seg)
            if doc.noun_chunks:
                task_name = next(iter(doc.noun_chunks)).text.strip()
            else:
                task_name = seg.strip()

        # Duration.
        duration_minutes = _extract_duration_minutes(seg, cfg)
        if duration_minutes is None:
            duration_minutes = _estimate_default_duration_minutes(task_name, cfg)

        task_type = "fixed" if time_hhmm else "flexible"

        task_obj: dict[str, Any] = {
            "task": task_name,
            "type": task_type,
        }
        if time_hhmm:
            task_obj["time"] = time_hhmm
        if duration_minutes is not None:
            task_obj["duration"] = _minutes_to_duration_str(int(duration_minutes))
        if deadline:
            task_obj["deadline"] = deadline

        out.append(task_obj)

    return out


def parse_tasks_from_lines(lines: Iterable[str], **kwargs: Any) -> list[dict[str, Any]]:
    """Convenience helper to parse multiple lines of input."""

    tasks: list[dict[str, Any]] = []
    for line in lines:
        line = (line or "").strip()
        if not line:
            continue
        tasks.extend(parse_tasks(line, **kwargs))
    return tasks
