"""One-shot LazyBrain wikilink backfill.

For each of the user's existing notes, this script:

1. Runs the existing ``wikilink_injector.inject`` over the body, so any
   place the note mentions another note's title gets wrapped in ``[[...]]``.
2. Computes the top-3 notes with the highest shared-tag count (ignoring
   system tags) and appends a "Related" footer with those titles as
   wikilinks — if a ``Related`` section doesn't already exist.

Run inside the lazyclaw container:

    docker exec lazyclaw python scripts/backfill_wikilinks.py <user_id>

Idempotent: running it twice is a no-op because the ``Related`` check
skips notes that already have one.
"""
from __future__ import annotations

import asyncio
import sys
from collections import Counter

from lazyclaw.config import load_config
from lazyclaw.lazybrain import store
from lazyclaw.runtime.wikilink_injector import inject, invalidate_cache

# Mirrors the frontend SYSTEM_TAG_PREFIXES — tags we don't consider
# "meaningful" when computing related notes.
SYSTEM_TAG_EXACT = {"auto"}
SYSTEM_TAG_PREFIXES = (
    "owner/", "source/", "kind/", "layer/", "imported/",
    "journal/", "priority/", "category/", "site/",
)


def meaningful_tags(tags: list[str] | None) -> set[str]:
    if not tags:
        return set()
    out = set()
    for t in tags:
        low = t.lower()
        if low in SYSTEM_TAG_EXACT:
            continue
        if any(low.startswith(p) for p in SYSTEM_TAG_PREFIXES):
            continue
        out.add(low)
    return out


async def backfill(user_id: str, *, dry_run: bool = False) -> None:
    config = load_config()
    notes = await store.list_notes(config, user_id, limit=2000)
    print(f"Loaded {len(notes)} notes for user {user_id[:8]}")

    # Title → id for wikilink injection + deduplication
    notes_by_id = {n["id"]: n for n in notes}
    tags_by_id = {n["id"]: meaningful_tags(n.get("tags")) for n in notes}

    updated = 0
    skipped_no_title = 0
    skipped_already = 0

    for note in notes:
        nid = note["id"]
        title = (note.get("title") or "").strip()
        content = note.get("content") or ""
        if not title:
            skipped_no_title += 1
            continue

        # Compute related notes by shared-tag count
        my_tags = tags_by_id[nid]
        scored: list[tuple[int, str, str]] = []
        if my_tags:
            for other_id, other in notes_by_id.items():
                if other_id == nid:
                    continue
                shared = len(my_tags & tags_by_id[other_id])
                if shared == 0:
                    continue
                other_title = (other.get("title") or "").strip()
                if not other_title:
                    continue
                scored.append((shared, other_title, other_id))
            scored.sort(reverse=True)

        top3 = [s for s in scored[:3]]

        # Pass 1: rewrite body with wikilink_injector (known-title match)
        new_content = await inject(config, user_id, content, max_rewrites=20)

        # Pass 2: append Related section if not already there and we have picks
        has_related = "### Related" in new_content or "## Related" in new_content
        if top3 and not has_related:
            related_lines = [f"- [[{t}]]" for _, t, _ in top3]
            related = "\n\n### Related\n" + "\n".join(related_lines)
            new_content = new_content.rstrip() + related

        if new_content == content:
            skipped_already += 1
            continue

        if dry_run:
            print(f"  WOULD UPDATE: {title[:60]}")
            continue

        await store.update_note(config, user_id, nid, content=new_content)
        updated += 1
        print(f"  + {title[:60]} (+{len(top3)} related)")

    invalidate_cache(user_id)
    print(
        f"\nDone. updated={updated} already_linked={skipped_already} "
        f"no_title={skipped_no_title}"
    )


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: python scripts/backfill_wikilinks.py <user_id> [--dry-run]")
        sys.exit(2)
    user_id = sys.argv[1]
    dry_run = "--dry-run" in sys.argv
    asyncio.run(backfill(user_id, dry_run=dry_run))


if __name__ == "__main__":
    main()
