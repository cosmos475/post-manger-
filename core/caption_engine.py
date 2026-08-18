"""
Caption transformation engine.

Pure logic, no Telegram API calls. Operates on caption text + entities
(as returned by the Bot API) and produces a new (text, entities) pair.

Critical detail: Telegram entity offsets are UTF-16 code units, not Python
string indices. All offset math here is done in UTF-16 space to avoid
corrupting entities when captions contain emoji or other non-BMP characters.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


# Entity types that represent a hyperlink on visible text.
LINK_ENTITY_TYPES = {"text_link", "url"}


@dataclass(frozen=True)
class CaptionEntity:
    """Mirrors Telegram's MessageEntity, restricted to fields we use."""

    type: str
    offset: int  # UTF-16 code units
    length: int  # UTF-16 code units
    url: str | None = None


@dataclass(frozen=True)
class TransformResult:
    text: str
    entities: list[CaptionEntity]
    links_removed: int
    words_replaced: int
    urls_removed: int = 0
    lines_removed: int = 0
    injected: bool = False
    hyperlink_added: bool = False
    quotes_removed: int = 0

    @property
    def changed(self) -> bool:
        return (
            self.links_removed > 0
            or self.words_replaced > 0
            or self.urls_removed > 0
            or self.lines_removed > 0
            or self.injected
            or self.hyperlink_added
            or self.quotes_removed > 0
        )


def _to_utf16(text: str) -> list[bytes]:
    """Encode text as UTF-16 code units, returned as a list of 2-byte chunks."""
    raw = text.encode("utf-16-le")
    return [raw[i : i + 2] for i in range(0, len(raw), 2)]  # noqa: E203


def _utf16_units_to_str(units: list[bytes]) -> str:
    return b"".join(units).decode("utf-16-le")


def _boundary_pattern(word: str) -> re.Pattern[str]:
    """
    Builds a match pattern for `word` with boundary rules that adapt to the
    word's first/last character:

    - If a side's boundary character is a word character (\\w: letters,
      digits, underscore), use standard \\b there. This preserves whole-word
      behavior for normal words -- "cat" won't match inside "concatenate".
    - If a side's boundary character is NOT a word character (e.g. "@" in
      "@username", an emoji, "#" in a hashtag, or a symbol like "★"), \\b
      can never be satisfied there, so no boundary constraint is applied on
      that side -- the literal search text is matched as-is.
    """
    escaped = re.escape(word)
    first_char = word[0]
    last_char = word[-1]

    left = r"\b" if re.match(r"\w", first_char, re.UNICODE) else ""
    right = r"\b" if re.match(r"\w", last_char, re.UNICODE) else ""

    return re.compile(left + escaped + right, re.IGNORECASE)


def _find_whole_word_matches_utf16(units: list[bytes], word: str) -> list[tuple[int, int]]:
    """
    Find whole-word (or whole-token, for symbol/emoji/@-led words),
    case-insensitive matches of `word` in the UTF-16 unit sequence. Returns
    list of (offset, length) in UTF-16 units.
    """
    text = _utf16_units_to_str(units)
    pattern = _boundary_pattern(word)

    matches: list[tuple[int, int]] = []
    for m in pattern.finditer(text):
        prefix_units = len(text[: m.start()].encode("utf-16-le")) // 2
        match_units = len(m.group(0).encode("utf-16-le")) // 2
        matches.append((prefix_units, match_units))
    return matches


def _entity_overlaps(entity: CaptionEntity, start: int, end: int) -> bool:
    """True if [start, end) overlaps entity's [offset, offset+length)."""
    e_start, e_end = entity.offset, entity.offset + entity.length
    return e_start < end and start < e_end


# Matches plain-text URLs of the supported forms only (scoped intentionally
# narrow to avoid false positives on ordinary text containing dots, e.g.
# "Node.js" or "file.txt"): http://, https://, www., t.me/, telegram.me/
# (the last two also matched bare, without a leading scheme). Generic bare
# domains (example.com, google.in) are explicitly out of scope.
URL_PATTERN = re.compile(
    r"(?:https?://\S+)"
    r"|(?:www\.\S+)"
    r"|(?:(?<![\w./])t\.me/\S+)"
    r"|(?:(?<![\w./])telegram\.me/\S+)",
    re.IGNORECASE,
)


def _find_url_matches_utf16(units: list[bytes]) -> list[tuple[int, int]]:
    """
    Find all direct-URL matches in the UTF-16 unit sequence. Returns list
    of (offset, length) in UTF-16 units, same convention as
    `_find_whole_word_matches_utf16`.

    Trailing punctuation commonly attached to a URL in prose (., ,, ), !,
    ?) is trimmed from the match so removing the URL doesn't eat the
    sentence's closing punctuation.
    """
    text = _utf16_units_to_str(units)
    matches: list[tuple[int, int]] = []
    for m in URL_PATTERN.finditer(text):
        match_text = m.group(0)
        end = m.end()
        while match_text and match_text[-1] in ".,)!?":
            match_text = match_text[:-1]
            end -= 1
        if not match_text:
            continue
        prefix_units = len(text[: m.start()].encode("utf-16-le")) // 2
        match_units = len(text[m.start() : end].encode("utf-16-le")) // 2  # noqa: E203
        matches.append((prefix_units, match_units))
    return matches


def remove_direct_urls(
    text: str, entities: list[CaptionEntity]
) -> tuple[str, list[CaptionEntity], int]:
    """
    Remove plain-text URLs (http://, https://, www., t.me/, telegram.me/)
    from visible caption text. Any Telegram entity overlapping a removed
    URL span is also dropped, so the resulting caption + entities remain
    valid for editMessageCaption (an entity pointing past deleted text
    would otherwise cause a Telegram API "can't parse entities" error) --
    one generic overlap-based rule, same mechanism already used by
    replace_word for link entities overlapping a replaced word.

    Whitespace immediately surrounding a removed URL is collapsed to a
    single space (or removed entirely at line start/end) to avoid leaving
    stray double-spaces -- localized strictly to the removal spot, so it
    cannot alter whitespace anywhere else in the caption.
    """
    units = _to_utf16(text)
    matches = _find_url_matches_utf16(units)
    if not matches:
        return text, entities, 0

    matches_sorted = sorted(matches, key=lambda m: m[0], reverse=True)

    working_units = list(units)
    working_entities = list(entities)
    removed_count = 0

    for match_start, match_len in matches_sorted:
        match_end = match_start + match_len

        survivors: list[CaptionEntity] = []
        for ent in working_entities:
            if _entity_overlaps(ent, match_start, match_end):
                continue
            survivors.append(ent)
        working_entities = survivors

        del working_units[match_start:match_end]

        # Collapse whitespace strictly at the spot the URL was removed from
        # (not the whole caption): if a space/tab now sits directly on both
        # sides of the deletion point, collapse that adjacent run down to a
        # single space; if the deletion point is now at the start/end of its
        # line, trim the adjacent run instead of leaving a leading/trailing
        # space. This is local to match_start only -- it cannot touch
        # whitespace anywhere else in the caption (e.g. inside a Find &
        # Replace block elsewhere in the text).
        collapse_start = match_start
        while collapse_start > 0 and working_units[collapse_start - 1] in (b" ", b"\t"):
            collapse_start -= 1
        collapse_end = match_start
        while collapse_end < len(working_units) and working_units[collapse_end] in (b" ", b"\t"):
            collapse_end += 1

        at_line_start = collapse_start == 0 or working_units[collapse_start - 1] == b"\n"
        at_line_end = collapse_end == len(working_units) or working_units[collapse_end] == b"\n"

        if collapse_end > collapse_start:
            if at_line_start or at_line_end:
                replacement_ws: list[bytes] = []
            else:
                replacement_ws = [" ".encode("utf-16-le")]
            removed_ws_len = collapse_end - collapse_start
            working_units[collapse_start:collapse_end] = replacement_ws
            ws_delta = len(replacement_ws) - removed_ws_len

            reshifted: list[CaptionEntity] = []
            for ent in working_entities:
                if ent.offset >= collapse_end:
                    reshifted.append(
                        CaptionEntity(
                            type=ent.type,
                            offset=ent.offset + ws_delta,
                            length=ent.length,
                            url=ent.url,
                        )
                    )
                else:
                    reshifted.append(ent)
            working_entities = reshifted

            length_delta = -match_len + ws_delta
        else:
            length_delta = -match_len

        shifted_entities: list[CaptionEntity] = []
        for ent in working_entities:
            if ent.offset >= match_end:
                shifted_entities.append(
                    CaptionEntity(
                        type=ent.type,
                        offset=ent.offset + length_delta,
                        length=ent.length,
                        url=ent.url,
                    )
                )
            else:
                shifted_entities.append(ent)
        working_entities = shifted_entities

        removed_count += 1

    new_text = _utf16_units_to_str(working_units)

    return new_text, working_entities, removed_count


def remove_all_links(
    text: str, entities: list[CaptionEntity]
) -> tuple[list[CaptionEntity], int]:
    """
    Strip link entities (text_link / url) while keeping visible text unchanged.
    """
    kept: list[CaptionEntity] = []
    removed = 0
    for ent in entities:
        if ent.type in LINK_ENTITY_TYPES:
            removed += 1
            continue
        kept.append(ent)
    return kept, removed


QUOTE_ENTITY_TYPES = {"blockquote", "expandable_blockquote"}


def remove_quotes(
    text: str, entities: list[CaptionEntity]
) -> tuple[str, list[CaptionEntity], int]:
    """
    Remove Telegram quote FORMATTING (both `blockquote` and
    `expandable_blockquote` entity types -- covering both a normal/expanded
    quote and a collapsed/expandable quote) while preserving the
    underlying caption text exactly as-is.

    This strips only the quote entity itself -- text, spacing, line
    breaks, and every other entity (including any nested inside the quote,
    e.g. a mention/bold/text_link) are left completely untouched. Since no
    text is removed, no offset shifting is needed for anything else.
    """
    removed_count = 0
    kept: list[CaptionEntity] = []
    for ent in entities:
        if ent.type in QUOTE_ENTITY_TYPES:
            removed_count += 1
            continue
        kept.append(ent)
    return text, kept, removed_count


def add_full_caption_hyperlink(
    text: str, entities: list[CaptionEntity], url: str
) -> list[CaptionEntity]:
    """
    Make the caption text clickable via a single configured URL. Must only
    ever be called as the last step of the pipeline, after the final text
    is fully settled (any earlier call would use a stale text length).

    Any existing text_link/url entities (regardless of source -- original
    caption, Find & Replace, or Caption Injector) are stripped first, since
    two text_link/url entities covering overlapping text is not a valid
    nesting relationship and is unsafe/undefined in Telegram clients.
    `mention` entities are stripped too -- the configured URL is meant to
    win over an old mention's profile-click destination, per spec.

    Per Telegram's entity nesting rules, only bold/italic/underline/
    strikethrough/spoiler entities may be contained inside another entity;
    blockquote/expandable_blockquote cannot be nested inside a text_link,
    and Telegram rejects the whole entity list if they are. Rather than
    stripping quote formatting to work around this, the link is instead
    split into multiple text_link entities that together cover the whole
    text EXCEPT any blockquote/expandable_blockquote span -- so the
    configured URL still applies to the entire caption (every clickable
    character reachable), while quote formatting is fully preserved.
    Bold/italic/underline/strikethrough/spoiler entities are left
    untouched -- they're valid to nest inside a text_link.

    Returns the original `entities` unchanged if `text` is empty/
    whitespace-only or `url` is falsy (no-op instead of adding a
    zero/invalid-span entity).
    """
    if not text.strip() or not url:
        return entities

    strip_types = LINK_ENTITY_TYPES | {"mention"}
    kept = [ent for ent in entities if ent.type not in strip_types]

    quote_spans = sorted(
        (ent.offset, ent.offset + ent.length) for ent in entities if ent.type in QUOTE_ENTITY_TYPES
    )
    full_length = len(_to_utf16(text))

    cursor = 0
    for q_start, q_end in quote_spans:
        q_start = max(0, min(q_start, full_length))
        q_end = max(q_start, min(q_end, full_length))
        if q_start > cursor:
            kept.append(CaptionEntity(type="text_link", offset=cursor, length=q_start - cursor, url=url))
        cursor = max(cursor, q_end)
    if cursor < full_length:
        kept.append(CaptionEntity(type="text_link", offset=cursor, length=full_length - cursor, url=url))

    return kept


def _find_promotional_line_spans(
    units: list[bytes], phrases: list[str]
) -> list[tuple[int, int]]:
    """
    Scan `units` (UTF-16 code units) line by line (split on the UTF-16 unit
    for "\\n") and return the (start, end) UTF-16 span -- including the
    line's own text but NOT its trailing newline -- of every line that
    contains any of `phrases` anywhere in it (case-insensitive, whole-word/
    whole-phrase match, not merely a first-word check). Regex special
    characters in a phrase are escaped so custom phrases are always treated
    as literal text.
    """
    if not phrases:
        return []

    newline_unit = "\n".encode("utf-16-le")
    line_spans: list[tuple[int, int]] = []
    line_start = 0
    for i, u in enumerate(units):
        if u == newline_unit:
            line_spans.append((line_start, i))
            line_start = i + 1
    line_spans.append((line_start, len(units)))

    compiled = [
        re.compile(r"(?<!\w)" + re.escape(p) + r"(?!\w)", re.IGNORECASE)
        for p in phrases
        if p
    ]
    if not compiled:
        return []

    matches: list[tuple[int, int]] = []
    for start, end in line_spans:
        line_text = _utf16_units_to_str(units[start:end])
        if any(pattern.search(line_text) for pattern in compiled):
            matches.append((start, end))
    return matches


def remove_promotional_lines(
    text: str, entities: list[CaptionEntity], phrases: list[str]
) -> tuple[str, list[CaptionEntity], int]:
    """
    Remove every line (delimited by "\\n") that contains any of `phrases`
    anywhere in it, case-insensitively, as a whole word/phrase (so "Owner"
    matches "Contact Owner" but not "Ownership"). The trailing newline of a
    removed line is removed along with it, so no blank line is introduced
    in its place; any blank lines this creates elsewhere (e.g. an original
    blank separator line now adjacent to another blank line) are collapsed
    afterward -- collapsing is limited to consecutive/leading/trailing
    blank lines only, never any other whitespace in the caption.

    Uses the same entity-aware classification as `replace_word()` --
    fully-before entities are untouched, fully-inside a removed line is
    dropped, fully-after is offset-shifted, and entities that partially
    overlap a removed line's boundary are clipped to their surviving
    portion(s) -- applied uniformly to every entity type. Removed line
    spans are processed in reverse document order so earlier offsets stay
    valid across iterations.
    """
    units = _to_utf16(text)
    line_matches = _find_promotional_line_spans(units, phrases)
    if not line_matches:
        return text, entities, 0

    # Each matched line span currently excludes its own trailing newline;
    # extend the deletion to consume that trailing newline too (or, for the
    # very last line with no trailing newline, the *preceding* newline),
    # so removing a line doesn't leave a stray blank line behind on its
    # own. This is still purely local to the removed line, not a global
    # pass.
    newline_unit = "\n".encode("utf-16-le")
    deletion_spans: list[tuple[int, int]] = []
    for start, end in line_matches:
        if end < len(units) and units[end] == newline_unit:
            deletion_spans.append((start, end + 1))
        elif start > 0 and units[start - 1] == newline_unit:
            deletion_spans.append((start - 1, end))
        else:
            # Sole remaining line (no newline anywhere) -- delete as is.
            deletion_spans.append((start, end))

    spans_sorted = sorted(deletion_spans, key=lambda s: s[0], reverse=True)

    working_units = list(units)
    working_entities = list(entities)
    removed_count = 0

    for span_start, span_end in spans_sorted:
        next_entities: list[CaptionEntity] = []
        for ent in working_entities:
            ent_start = ent.offset
            ent_end = ent.offset + ent.length

            if ent_end <= span_start:
                next_entities.append(ent)
                continue

            if ent_start >= span_end:
                next_entities.append(
                    CaptionEntity(
                        type=ent.type,
                        offset=ent_start - (span_end - span_start),
                        length=ent.length,
                        url=ent.url,
                    )
                )
                continue

            if ent_start >= span_start and ent_end <= span_end:
                # Fully inside the removed line: its text is gone.
                continue

            left_part_len = max(0, span_start - ent_start)
            right_part_start = max(ent_start, span_end)
            right_part_len = max(0, ent_end - right_part_start)

            if left_part_len > 0:
                next_entities.append(
                    CaptionEntity(type=ent.type, offset=ent_start, length=left_part_len, url=ent.url)
                )
            if right_part_len > 0:
                next_entities.append(
                    CaptionEntity(
                        type=ent.type,
                        offset=right_part_start - (span_end - span_start),
                        length=right_part_len,
                        url=ent.url,
                    )
                )

        working_entities = next_entities
        del working_units[span_start:span_end]
        removed_count += 1

    new_text = _utf16_units_to_str(working_units)

    # Collapse blank-line buildup caused directly by the removals above:
    # 2+ consecutive newlines (with only whitespace between them) down to
    # exactly one blank line, and strip any leading/trailing blank lines.
    # This never touches whitespace within a line (e.g. "Owner      •").
    # Because this can shift/remove text before surviving entities, redo it
    # at the UTF-16-unit level (matching the rest of this module's offset
    # convention) so entity offsets stay in sync with the final text,
    # instead of collapsing the string and leaving offsets stale.
    collapsed_units = _to_utf16(new_text)

    # Leading blank lines: count UTF-16 units to strip from the start (only
    # newlines/spaces/tabs before the first non-blank line).
    lead_strip = 0
    i = 0
    space_unit = " ".encode("utf-16-le")
    tab_unit = "\t".encode("utf-16-le")
    while i < len(collapsed_units) and collapsed_units[i] in (newline_unit, space_unit, tab_unit):
        i += 1
        lead_strip = i
    # Only actually strip up to the last newline in that blank-line run
    # (so a genuinely blank *first line* is removed, but we don't eat into
    # leading spaces of the first real content line).
    last_newline_in_lead = -1
    for j in range(lead_strip):
        if collapsed_units[j] == newline_unit:
            last_newline_in_lead = j
    lead_strip = last_newline_in_lead + 1 if last_newline_in_lead >= 0 else 0

    # Trailing blank lines: symmetric, from the end.
    trail_strip = 0
    i = len(collapsed_units)
    while i > lead_strip and collapsed_units[i - 1] in (newline_unit, space_unit, tab_unit):
        i -= 1
        trail_strip += 1
    first_newline_in_trail = -1
    for j in range(len(collapsed_units) - trail_strip, len(collapsed_units)):
        if collapsed_units[j] == newline_unit:
            first_newline_in_trail = j
            break
    trail_strip = (len(collapsed_units) - first_newline_in_trail) if first_newline_in_trail >= 0 else 0

    body_start = lead_strip
    body_end = len(collapsed_units) - trail_strip
    body_units = collapsed_units[body_start:body_end]

    # Collapse runs of 3+ consecutive newlines (i.e. 2+ blank lines) down to
    # exactly 2 newlines (1 blank line) within the body, tracking a
    # cumulative per-position shift so entity offsets can be remapped.
    final_units: list[bytes] = []
    shift_at: list[int] = []  # shift_at[k] = cumulative units removed by position k in body_units
    run = 0
    cum_removed = 0
    for u in body_units:
        shift_at.append(cum_removed)
        if u == newline_unit:
            run += 1
            if run <= 2:
                final_units.append(u)
            else:
                cum_removed += 1
        else:
            run = 0
            final_units.append(u)
    shift_at.append(cum_removed)  # sentinel for offset == len(body_units)

    def _remap(offset: int) -> int:
        # Map an offset in collapsed_units (pre lead/trail-strip, pre
        # blank-run-collapse) to its position in final_units.
        body_off = offset - body_start
        body_off = max(0, min(body_off, len(body_units)))
        return body_off - shift_at[body_off]

    remapped_entities: list[CaptionEntity] = []
    final_len = len(final_units)
    for ent in working_entities:
        new_start = _remap(ent.offset)
        new_end = _remap(ent.offset + ent.length)
        new_start = max(0, min(new_start, final_len))
        new_end = max(new_start, min(new_end, final_len))
        if new_end > new_start:
            remapped_entities.append(
                CaptionEntity(type=ent.type, offset=new_start, length=new_end - new_start, url=ent.url)
            )
    working_entities = remapped_entities

    new_text = _utf16_units_to_str(final_units)

    return new_text, working_entities, removed_count


def replace_word(
    text: str,
    entities: list[CaptionEntity],
    find_word: str,
    replace_word_with: str,
    replace_word_entities: list[CaptionEntity] | None = None,
) -> tuple[str, list[CaptionEntity], int]:
    """
    Whole-word (or whole-token), case-insensitive replace of every
    occurrence of `find_word` with `replace_word_with`.

    Every entity (of any type -- mention, text_link, url, bold, italic,
    blockquote, etc.) is classified against each match: fully before the
    match is left unchanged; fully inside the match is dropped (its
    underlying text is gone); fully after the match is offset-shifted by
    the replacement's length delta; and an entity that partially overlaps
    the match boundary is clipped to its surviving portion(s) outside the
    match, with offsets recalculated accordingly. This keeps every
    resulting entity's offset/length valid against the post-replacement
    text, regardless of entity type.

    If `replace_word_entities` is provided, those entities (offsets
    relative to the start of `replace_word_with` itself) are re-anchored to
    each match position and inserted on top of the above. This preserves
    formatting -- most commonly a hyperlink -- that the user applied to
    only part of the replacement text (e.g. only "ALEX" linked in "ALEX is
    King", or only "JOIN ME" linked in "JOIN ME on insta").
    """
    units = _to_utf16(text)
    matches = _find_whole_word_matches_utf16(units, find_word)
    if not matches:
        return text, entities, 0

    replace_units = _to_utf16(replace_word_with)
    replace_word_entities = replace_word_entities or []

    matches_sorted = sorted(matches, key=lambda m: m[0], reverse=True)

    working_units = list(units)
    working_entities = list(entities)
    replaced_count = 0

    for match_start, match_len in matches_sorted:
        match_end = match_start + match_len
        length_delta = len(replace_units) - match_len

        # Entity-aware classification against this match, applied uniformly
        # to every entity type (no type-based skip) -- fully-before entities
        # are untouched, fully-contained entities are dropped, fully-after
        # entities are shifted by length_delta, and entities that partially
        # overlap the match boundary are clipped to their surviving portion
        # (with offsets recalculated) rather than being dropped outright or
        # left stale. This keeps every entity's offset/length valid against
        # the post-replacement text.
        next_entities: list[CaptionEntity] = []
        for ent in working_entities:
            ent_start = ent.offset
            ent_end = ent.offset + ent.length

            if ent_end <= match_start:
                # Fully-before: unaffected by this match.
                next_entities.append(ent)
                continue

            if ent_start >= match_end:
                # Fully-after: shift by the length delta introduced by this
                # match's replacement.
                next_entities.append(
                    CaptionEntity(
                        type=ent.type,
                        offset=ent_start + length_delta,
                        length=ent.length,
                        url=ent.url,
                    )
                )
                continue

            if ent_start >= match_start and ent_end <= match_end:
                # Fully-contained inside the matched span: the span itself
                # is being replaced, so this entity's underlying text is
                # gone -- drop it.
                continue

            # Partial-overlap: entity crosses the match boundary on one or
            # both sides. Clip it to whichever portion(s) fall outside the
            # matched span, then account for the replacement's length delta
            # like a fully-after entity would need if any surviving portion
            # is on the right side.
            left_part_len = max(0, match_start - ent_start)
            right_part_start = max(ent_start, match_end)
            right_part_len = max(0, ent_end - right_part_start)

            if left_part_len > 0:
                # Surviving portion is the slice before match_start; offset
                # unchanged since it's entirely before the match.
                next_entities.append(
                    CaptionEntity(
                        type=ent.type,
                        offset=ent_start,
                        length=left_part_len,
                        url=ent.url,
                    )
                )
            if right_part_len > 0:
                # Surviving portion is the slice after match_end; shift by
                # length_delta same as a fully-after entity.
                next_entities.append(
                    CaptionEntity(
                        type=ent.type,
                        offset=right_part_start + length_delta,
                        length=right_part_len,
                        url=ent.url,
                    )
                )
            # If both parts are zero-length (shouldn't happen given the
            # containment check above already handled full containment),
            # the entity is effectively dropped.

        working_entities = next_entities

        working_units[match_start:match_end] = list(replace_units)

        # Re-anchor any entities carried on the replacement text itself
        # (e.g. a hyperlink on just part of it) to this match's position.
        for rep_ent in replace_word_entities:
            working_entities.append(
                CaptionEntity(
                    type=rep_ent.type,
                    offset=match_start + rep_ent.offset,
                    length=rep_ent.length,
                    url=rep_ent.url,
                )
            )

        replaced_count += 1

    new_text = _utf16_units_to_str(working_units)
    return new_text, working_entities, replaced_count


def inject_text(
    text: str,
    entities: list[CaptionEntity],
    inject_text_value: str,
    inject_text_entities: list[CaptionEntity] | None = None,
) -> tuple[str, list[CaptionEntity]]:
    """
    Append `inject_text_value` to the bottom of `text`. Existing entity
    offsets are unaffected since text is appended after them -- no shift
    needed. Always the final step of the transform pipeline.

    `inject_text_entities` (offsets relative to the start of
    inject_text_value itself) are re-anchored to the injected text's actual
    position in the final caption and appended alongside the existing
    entities, preserving any formatting -- most commonly a hyperlink on
    only part of the injected text -- that the user applied when the text
    was set.
    """
    if not inject_text_value:
        return text, entities

    separator = "\n\n"
    prefix_units = len(_to_utf16(text + separator))
    new_text = text + separator + inject_text_value

    new_entities = list(entities)
    for ent in inject_text_entities or []:
        new_entities.append(
            CaptionEntity(
                type=ent.type,
                offset=prefix_units + ent.offset,
                length=ent.length,
                url=ent.url,
            )
        )

    return new_text, new_entities


def transform_caption(
    text: str,
    entities: list[CaptionEntity],
    find_word: str,
    replace_word_with: str,
    remove_links: bool,
    inject_text_value: str | None = None,
    remove_urls: bool = False,
    replace_word_entities: list[CaptionEntity] | None = None,
    inject_text_entities: list[CaptionEntity] | None = None,
    promo_phrases: list[str] | None = None,
    add_hyperlink_url: str | None = None,
    remove_quotes_enabled: bool = False,
) -> TransformResult:
    """
    Apply the full configured transformation pipeline to a single caption,
    in order:
    1. Remove Direct URLs (if enabled) -- strips plain-text URLs and any
       overlapping entity.
    2. Remove Hyperlinks (if enabled) -- strips all link entities globally.
    3. Quote Removal (if enabled) -- strips blockquote/
       expandable_blockquote FORMATTING only; the underlying text is
       always preserved unchanged.
    4. Promotional Line Remover (if `promo_phrases` non-empty) -- removes
       any whole line containing a configured phrase anywhere in it,
       case-insensitively, entity-safe.
    5. Find & Replace (if enabled) -- whole-word replace; any remaining
       link entity overlapping a replaced word is stripped too (safe
       regardless of whether earlier steps already ran). If
       `replace_word_entities` is provided, those entities (e.g. a
       hyperlink on part of the replacement text) are preserved instead of
       being dropped.
    6. Caption Injector (if inject_text_value provided) -- appended at the
       bottom. Off by default (None/empty leaves existing behavior
       completely unchanged). If `inject_text_entities` is provided, that
       formatting is preserved.
    7. Add Hyperlink (if add_hyperlink_url provided) -- ALWAYS the final
       step. Wraps the entire final caption text in one text_link to the
       configured URL, stripping any existing text_link/url entities
       first. No-op if the final text is empty/whitespace-only.
    """
    working_text = text
    working_entities = entities
    words_replaced = 0
    links_removed = 0
    urls_removed = 0
    lines_removed = 0
    quotes_removed = 0

    if remove_urls:
        working_text, working_entities, urls_removed = remove_direct_urls(working_text, working_entities)

    if remove_links:
        working_entities, links_removed = remove_all_links(working_text, working_entities)

    if remove_quotes_enabled:
        working_text, working_entities, quotes_removed = remove_quotes(working_text, working_entities)

    if promo_phrases:
        working_text, working_entities, lines_removed = remove_promotional_lines(
            working_text, working_entities, promo_phrases
        )

    if find_word:
        working_text, working_entities, words_replaced = replace_word(
            working_text, working_entities, find_word, replace_word_with, replace_word_entities
        )

    injected = False
    if inject_text_value:
        working_text, working_entities = inject_text(
            working_text, working_entities, inject_text_value, inject_text_entities
        )
        injected = True

    hyperlink_added = False
    if add_hyperlink_url and working_text.strip():
        working_entities = add_full_caption_hyperlink(working_text, working_entities, add_hyperlink_url)
        hyperlink_added = True

    return TransformResult(
        text=working_text,
        entities=working_entities,
        links_removed=links_removed,
        words_replaced=words_replaced,
        urls_removed=urls_removed,
        lines_removed=lines_removed,
        injected=injected,
        hyperlink_added=hyperlink_added,
        quotes_removed=quotes_removed,
    )


def is_skippable(text: str | None) -> bool:
    """
    Cheap pre-check: True if caption is empty/missing.
    """
    return not text
