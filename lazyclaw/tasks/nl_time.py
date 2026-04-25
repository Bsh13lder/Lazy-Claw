"""Natural-language time parser for the quick-add task input.

Scope: the top ~10 phrases users actually type — "tomorrow", "today at 9",
"in 2 hours", "next Monday", plus Spanish equivalents (the primary user is
in Madrid). Anything more elaborate falls through to the LLM-backed
``ai_parse`` module, so this file stays small, synchronous, and fast.

Output shape: a dict with
    - ``due_date``: YYYY-MM-DD or None
    - ``reminder_at``: ISO-8601 datetime (UTC) or None
    - ``remaining``: the input with the matched time phrase stripped out
    - ``matched``: the phrase we recognized (for the UI ghost preview)

Returning ``remaining`` lets the caller use the stripped text as the task
title, so the user can type "tomorrow at 9 buy milk" and get a "buy milk"
task due tomorrow 09:00 without extra plumbing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Callable

from dateutil import parser as dateutil_parser

# Madrid is the user's primary timezone (Europe/Madrid, UTC+1/+2).
# When the input carries no timezone, we assume the user's local one. We
# store everything as UTC ISO internally — the UI converts for display.
try:
    from zoneinfo import ZoneInfo
    _LOCAL_TZ = ZoneInfo("Europe/Madrid")
except Exception:  # pragma: no cover - zoneinfo always present on 3.9+
    _LOCAL_TZ = timezone.utc


WEEKDAYS_EN = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thur": 3, "thurs": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}
WEEKDAYS_ES = {
    "lunes": 0, "martes": 1, "miércoles": 2, "miercoles": 2,
    "jueves": 3, "viernes": 4, "sábado": 5, "sabado": 5, "domingo": 6,
}
WEEKDAYS = {**WEEKDAYS_EN, **WEEKDAYS_ES}


@dataclass(frozen=True)
class ParsedTime:
    due_date: str | None
    reminder_at: str | None
    remaining: str
    matched: str | None

    def as_dict(self) -> dict:
        return {
            "due_date": self.due_date,
            "reminder_at": self.reminder_at,
            "remaining": self.remaining,
            "matched": self.matched,
        }


def _to_utc_iso(dt_local: datetime) -> str:
    """Serialize a local-timezone datetime to UTC ISO-8601."""
    if dt_local.tzinfo is None:
        dt_local = dt_local.replace(tzinfo=_LOCAL_TZ)
    return dt_local.astimezone(timezone.utc).isoformat()


def _today_local() -> date:
    return datetime.now(_LOCAL_TZ).date()


def _combine_local(d: date, t: time | None) -> datetime:
    """Build a local-timezone datetime from a date and optional time.

    When no time is given we default to 09:00 local — matches what most
    todo apps do for "tomorrow" with no explicit hour.
    """
    return datetime.combine(d, t or time(9, 0), tzinfo=_LOCAL_TZ)


def _extract_time(text: str) -> tuple[time | None, str]:
    """Pull out an explicit "HH:MM" or "Hpm" time if present.

    Returns (time-or-None, text-with-the-phrase-removed).
    """
    # 24h "HH:MM"
    m = re.search(r"\b(?:at\s+|a\s+las\s+)?(\d{1,2}):(\d{2})\b", text, re.IGNORECASE)
    if m:
        hh, mm = int(m.group(1)), int(m.group(2))
        if 0 <= hh < 24 and 0 <= mm < 60:
            return time(hh, mm), (text[: m.start()] + text[m.end():]).strip()

    # 12h "3pm", "3am", "3 pm"
    m = re.search(r"\b(?:at\s+|a\s+las\s+)?(\d{1,2})\s?(am|pm)\b", text, re.IGNORECASE)
    if m:
        hh = int(m.group(1)) % 12
        if m.group(2).lower() == "pm":
            hh += 12
        return time(hh, 0), (text[: m.start()] + text[m.end():]).strip()

    # Bare hour anchored to "at" / "a las" — "at 9", "a las 15". Only accept
    # when anchored so we don't grab arbitrary numbers from the title.
    m = re.search(r"\b(?:at|a\s+las)\s+(\d{1,2})\b(?!\s*:)", text, re.IGNORECASE)
    if m:
        hh = int(m.group(1))
        if 0 <= hh < 24:
            return time(hh, 0), (text[: m.start()] + text[m.end():]).strip()

    return None, text


def _strip_phrase(text: str, start: int, end: int) -> str:
    """Remove [start:end] from text and tidy spacing."""
    return re.sub(r"\s+", " ", (text[:start] + " " + text[end:])).strip()


# ---------------------------------------------------------------------------
# Rules — ordered. First match wins.
# ---------------------------------------------------------------------------

Rule = Callable[[str], ParsedTime | None]


def _rule_tomorrow(text: str) -> ParsedTime | None:
    m = re.search(r"\b(tomorrow|mañana|manana)\b", text, re.IGNORECASE)
    if not m:
        return None
    rest = _strip_phrase(text, m.start(), m.end())
    t, rest = _extract_time(rest)
    d = _today_local() + timedelta(days=1)
    dt_local = _combine_local(d, t)
    return ParsedTime(
        due_date=d.isoformat(),
        reminder_at=_to_utc_iso(dt_local),
        remaining=rest,
        matched=m.group(0),
    )


def _rule_today(text: str) -> ParsedTime | None:
    m = re.search(r"\b(today|hoy)\b", text, re.IGNORECASE)
    if not m:
        return None
    rest = _strip_phrase(text, m.start(), m.end())
    t, rest = _extract_time(rest)
    d = _today_local()
    # If no explicit time and the default 09:00 already passed, push reminder
    # to "one hour from now" so the user doesn't get an instantly-overdue reminder.
    dt_local = _combine_local(d, t)
    if t is None and dt_local < datetime.now(_LOCAL_TZ):
        dt_local = datetime.now(_LOCAL_TZ) + timedelta(hours=1)
    return ParsedTime(
        due_date=d.isoformat(),
        reminder_at=_to_utc_iso(dt_local),
        remaining=rest,
        matched=m.group(0),
    )


def _rule_in_duration(text: str) -> ParsedTime | None:
    # "in 2 hours", "within 2 days", "after 2 hours", "en 2 horas",
    # "dentro de 3 días", "por 2 días", "before 30 minutes" — all collapse
    # to the same semantics (deadline = now + N units). Spanish "por" is a
    # bit loose but the worst case is over-matching, which the title
    # rebuilder cleans up.
    pattern = re.compile(
        r"\b(?:in|after|within|before|en|dentro\s+de|por)\s+(\d+)\s*"
        r"(minutes?|mins?|hours?|hrs?|hs?|days?|weeks?|"
        r"minutos?|horas?|días?|dias?|semanas?)\b",
        re.IGNORECASE,
    )
    m = pattern.search(text)
    if not m:
        return None
    qty = int(m.group(1))
    unit = m.group(2).lower()
    if unit.startswith(("min", "minu")):
        delta = timedelta(minutes=qty)
    elif unit.startswith(("hour", "hr", "h", "hora")):
        delta = timedelta(hours=qty)
    elif unit.startswith(("day", "día", "dia")):
        delta = timedelta(days=qty)
    elif unit.startswith(("week", "semana")):
        delta = timedelta(weeks=qty)
    else:
        return None
    now_local = datetime.now(_LOCAL_TZ)
    dt_local = now_local + delta
    rest = _strip_phrase(text, m.start(), m.end())
    return ParsedTime(
        due_date=dt_local.date().isoformat(),
        reminder_at=_to_utc_iso(dt_local),
        remaining=rest,
        matched=m.group(0),
    )


def _rule_n_unit_deadline(text: str) -> ParsedTime | None:
    """Match "2 days deadline", "1 day deadline", "3 horas límite".

    Phrasing the agent or user types when stating *how long they have*
    rather than *when something is due*. Resolves to now + N units.
    """
    pattern = re.compile(
        r"\b(\d+)\s*(minutes?|mins?|hours?|hrs?|days?|weeks?|"
        r"minutos?|horas?|días?|dias?|semanas?)\s+"
        r"(deadline|deadlines?|due|max|limit|límite|limite)\b",
        re.IGNORECASE,
    )
    m = pattern.search(text)
    if not m:
        return None
    qty = int(m.group(1))
    unit = m.group(2).lower()
    if unit.startswith(("min", "minu")):
        delta = timedelta(minutes=qty)
    elif unit.startswith(("hour", "hr", "hora")):
        delta = timedelta(hours=qty)
    elif unit.startswith(("day", "día", "dia")):
        delta = timedelta(days=qty)
    elif unit.startswith(("week", "semana")):
        delta = timedelta(weeks=qty)
    else:
        return None
    now_local = datetime.now(_LOCAL_TZ)
    dt_local = now_local + delta
    rest = _strip_phrase(text, m.start(), m.end())
    return ParsedTime(
        due_date=dt_local.date().isoformat(),
        reminder_at=_to_utc_iso(dt_local),
        remaining=rest,
        matched=m.group(0),
    )


def _rule_by_weekday(text: str) -> ParsedTime | None:
    """Match "by Friday", "before Monday", "antes del viernes".

    Resolves to the *coming* weekday (today if matches and not past 09:00,
    else the next occurrence). Distinct from "next Monday" which always
    pushes a week forward.
    """
    names = "|".join(sorted(WEEKDAYS.keys(), key=len, reverse=True))
    pattern = re.compile(
        rf"\b(?:by|before|antes\s+de(?:l)?)\s+({names})\b",
        re.IGNORECASE,
    )
    m = pattern.search(text)
    if not m:
        return None
    target = WEEKDAYS[m.group(1).lower()]
    today = _today_local()
    days = (target - today.weekday()) % 7
    # Same weekday as today and it's still morning → keep today; otherwise
    # roll forward. Avoids "by Monday" (typed Monday afternoon) silently
    # resolving to today and being instantly overdue.
    d = today + timedelta(days=days if days > 0 else 7)
    if days == 0 and datetime.now(_LOCAL_TZ).time() < time(9, 0):
        d = today
    rest = _strip_phrase(text, m.start(), m.end())
    t, rest = _extract_time(rest)
    dt_local = _combine_local(d, t)
    return ParsedTime(
        due_date=d.isoformat(),
        reminder_at=_to_utc_iso(dt_local),
        remaining=rest,
        matched=m.group(0),
    )


def _rule_next_weekday(text: str) -> ParsedTime | None:
    names = "|".join(sorted(WEEKDAYS.keys(), key=len, reverse=True))
    pattern = re.compile(
        rf"\b(?:next|próximo|proximo|on|el)\s+({names})\b",
        re.IGNORECASE,
    )
    m = pattern.search(text)
    if not m:
        return None
    target = WEEKDAYS[m.group(1).lower()]
    today = _today_local()
    days = (target - today.weekday()) % 7
    # "next X" = the coming X, and if today matches, push a week forward.
    if days == 0:
        days = 7
    d = today + timedelta(days=days)
    rest = _strip_phrase(text, m.start(), m.end())
    t, rest = _extract_time(rest)
    dt_local = _combine_local(d, t)
    return ParsedTime(
        due_date=d.isoformat(),
        reminder_at=_to_utc_iso(dt_local),
        remaining=rest,
        matched=m.group(0),
    )


def _rule_tonight(text: str) -> ParsedTime | None:
    m = re.search(r"\b(tonight|esta\s+noche)\b", text, re.IGNORECASE)
    if not m:
        return None
    rest = _strip_phrase(text, m.start(), m.end())
    t, rest = _extract_time(rest)
    # Default to 20:00 local if no time is explicit.
    dt_local = _combine_local(_today_local(), t or time(20, 0))
    return ParsedTime(
        due_date=_today_local().isoformat(),
        reminder_at=_to_utc_iso(dt_local),
        remaining=rest,
        matched=m.group(0),
    )


def _rule_bare_time(text: str) -> ParsedTime | None:
    """Match a time-only phrase like "at 2pm" / "a las 15:30" with no day anchor.

    Defaults to today if the time is still ahead, or tomorrow if it's past.
    """
    t, rest = _extract_time(text)
    if t is None:
        return None
    # Only trigger when the user typed an explicit anchor ("at", "a las")
    # in the original text — we don't want to grab random digits.
    if not re.search(r"\b(at|a\s+las)\b", text, re.IGNORECASE):
        return None
    d = _today_local()
    candidate = _combine_local(d, t)
    if candidate < datetime.now(_LOCAL_TZ):
        d = d + timedelta(days=1)
        candidate = _combine_local(d, t)
    return ParsedTime(
        due_date=d.isoformat(),
        reminder_at=_to_utc_iso(candidate),
        remaining=rest,
        matched=None,
    )


def _rule_absolute_date(text: str) -> ParsedTime | None:
    """Fallback: try dateutil for absolute phrases like "Apr 24", "24/04".

    dateutil is lenient, so we only accept results that contain at least
    one digit in the input — otherwise bare words ("urgent") would parse
    as today's date.
    """
    if not re.search(r"\d", text):
        return None
    try:
        # dateutil's fuzzy mode also returns the tokens it consumed so we
        # can strip them out of the remaining text.
        parsed, tokens = dateutil_parser.parse(
            text, default=_combine_local(_today_local(), time(9, 0)),
            fuzzy_with_tokens=True,
        )
    except (ValueError, OverflowError):
        return None

    # Reconstruct the remaining text from the "unused" tokens.
    rest = " ".join(t.strip() for t in tokens if t.strip())
    rest = re.sub(r"\s+", " ", rest).strip()

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_LOCAL_TZ)

    return ParsedTime(
        due_date=parsed.date().isoformat(),
        reminder_at=_to_utc_iso(parsed),
        remaining=rest,
        matched=None,  # dateutil doesn't hand back the exact matched substring
    )


_RULES: tuple[Rule, ...] = (
    _rule_tomorrow,
    _rule_today,
    _rule_tonight,
    # "2 days deadline" must beat _rule_in_duration which would otherwise
    # not even match (no preposition), and beat _rule_absolute_date which
    # would interpret a bare "2" as a day-of-month.
    _rule_n_unit_deadline,
    _rule_in_duration,
    # "by Friday" must beat _rule_next_weekday which only matches "next/on/el".
    _rule_by_weekday,
    _rule_next_weekday,
    _rule_bare_time,
    _rule_absolute_date,
)


def parse(text: str) -> ParsedTime:
    """Parse a free-form user phrase. Always returns a ParsedTime.

    When no rule matches, ``due_date`` and ``reminder_at`` are ``None`` and
    ``remaining`` is the original text.
    """
    text = text.strip()
    if not text:
        return ParsedTime(None, None, "", None)
    for rule in _RULES:
        result = rule(text)
        if result is not None:
            return result
    return ParsedTime(None, None, text, None)


# ---------------------------------------------------------------------------
# Priority + tag sniffing — cheap companions to the time parser. Keep the
# rules here so the quick-add flow has a single regex-based fast path.
# ---------------------------------------------------------------------------

PRIORITY_WORDS = {
    "urgent": "urgent", "urgente": "urgent", "asap": "urgent", "!!": "urgent",
    "high": "high", "alta": "high", "important": "high",
    "low": "low", "baja": "low",
}


def extract_priority(text: str) -> tuple[str | None, str]:
    """Pull a priority keyword out of the text if present.

    Matches whole words only so "highway" stays in the title.
    """
    lowered = text.lower()
    for word, pri in PRIORITY_WORDS.items():
        pattern = re.compile(rf"(?<!\w){re.escape(word)}(?!\w)", re.IGNORECASE)
        m = pattern.search(lowered)
        if m:
            stripped = _strip_phrase(text, m.start(), m.end())
            return pri, stripped
    return None, text


def extract_tags(text: str) -> tuple[list[str], str]:
    """Hashtag-style tag pull: #shopping, #work."""
    tags = re.findall(r"#([\w\-]+)", text)
    if not tags:
        return [], text
    cleaned = re.sub(r"#[\w\-]+", "", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return tags, cleaned


def parse_full(text: str) -> dict:
    """Parse time + priority + tags in one pass — the quick-add entry point."""
    time_result = parse(text)
    priority, working = extract_priority(time_result.remaining)
    tags, working = extract_tags(working)
    title = working.strip()
    return {
        "title": title,
        "due_date": time_result.due_date,
        "reminder_at": time_result.reminder_at,
        "priority": priority,
        "tags": tags,
        "matched_time": time_result.matched,
    }
