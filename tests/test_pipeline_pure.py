"""Pure pipeline logic — no I/O, no model calls.

Ported case-for-case from the vitest suite:
  tests/alignSegmentTags.test.ts
  tests/mergeOther.test.ts
  tests/reassignOther.test.ts

Plus consolidate/gaps coverage, which the Node suite never had.

Segments are plain dicts with the SAME camelCase keys the Node service writes,
because these structures are persisted verbatim into Video.topic_segments and
Video.transcript_segments and then read straight back out by the response
builder. Renaming a key here silently corrupts stored data.
"""

from app.pipeline.align_segment_tags import align_segment_tags
from app.pipeline.consolidate import consolidate_chapters
from app.pipeline.gaps import chunk_long_gaps, find_unspoken_gaps
from app.pipeline.merge_other import merge_orphan_other
from app.pipeline.reassign_other import parse_reassignments


def tx(start, end, main_tag, sub_tag=""):
    return {
        "id": start,
        "start": start,
        "end": end,
        "text": "t",
        "mainTag": main_tag,
        "subTag": sub_tag,
    }


def ch(start, end, main_tag):
    return {
        "mainTag": main_tag,
        "subTag": "",
        "start": start,
        "end": end,
        "thumbnailPath": None,
    }


def seg(start, end, main_tag):
    return {"id": start, "start": start, "end": end, "text": "", "mainTag": main_tag, "subTag": ""}


# ─── align_segment_tags ───────────────────────────────────────────────────────


def test_gives_each_transcript_segment_the_phase_of_its_chapter():
    out = align_segment_tags(
        [tx(0, 5, "other"), tx(5, 9, "other"), tx(20, 25, "other")],
        [ch(0, 10, "electrical check"), ch(10, 30, "air supply check")],
    )
    assert [s["mainTag"] for s in out] == [
        "electrical check",
        "electrical check",
        "air supply check",
    ]


def test_each_segment_keeps_its_own_sub_tag():
    """Only mainTag is propagated — subTag describes this exact moment."""
    out = align_segment_tags([tx(0, 5, "other", "checking the coil")], [ch(0, 10, "electrical")])
    assert out[0]["subTag"] == "checking the coil"


def test_leaves_a_segment_alone_when_no_chapter_contains_it():
    out = align_segment_tags([tx(50, 55, "diagnosis")], [ch(0, 10, "introduction")])
    assert out[0]["mainTag"] == "diagnosis"


def test_returns_segments_unchanged_when_there_are_no_chapters():
    out = align_segment_tags([tx(0, 5, "other")], [])
    assert out[0]["mainTag"] == "other"


def test_does_not_overwrite_with_an_empty_chapter_tag():
    out = align_segment_tags([tx(0, 5, "diagnosis")], [ch(0, 10, "")])
    assert out[0]["mainTag"] == "diagnosis"


def test_align_does_not_mutate_its_input():
    segments = [tx(0, 5, "other")]
    align_segment_tags(segments, [ch(0, 10, "repair")])
    assert segments[0]["mainTag"] == "other"


# ─── merge_orphan_other ───────────────────────────────────────────────────────


def test_absorbs_a_short_lone_other_between_two_same_tag_segments():
    out = merge_orphan_other([seg(0, 10, "repair"), seg(10, 15, "other"), seg(15, 25, "repair")])
    assert [s["mainTag"] for s in out] == ["repair", "repair", "repair"]


def test_leaves_a_long_other_alone():
    """30s of 'other' is real content, not filler."""
    out = merge_orphan_other([seg(0, 10, "repair"), seg(10, 40, "other"), seg(40, 50, "repair")])
    assert [s["mainTag"] for s in out] == ["repair", "other", "repair"]


def test_leaves_an_other_between_differing_tags_alone():
    out = merge_orphan_other([seg(0, 10, "diagnosis"), seg(10, 13, "other"), seg(15, 25, "repair")])
    assert [s["mainTag"] for s in out] == ["diagnosis", "other", "repair"]


def test_leaves_a_leading_or_trailing_other_alone():
    """No flanking pair means nothing to absorb into."""
    out = merge_orphan_other([seg(0, 3, "other"), seg(3, 10, "repair"), seg(10, 12, "other")])
    assert [s["mainTag"] for s in out] == ["other", "repair", "other"]


def test_merge_respects_a_custom_threshold():
    segs = [seg(0, 10, "repair"), seg(10, 20, "other"), seg(20, 30, "repair")]
    assert [s["mainTag"] for s in merge_orphan_other(segs, max_secs=8)] == [
        "repair",
        "other",
        "repair",
    ]
    assert [s["mainTag"] for s in merge_orphan_other(segs, max_secs=15)] == [
        "repair",
        "repair",
        "repair",
    ]


# ─── parse_reassignments ──────────────────────────────────────────────────────

ALLOWED = ["air supply check", "electrical check", "mechanical check"]


def test_maps_each_index_to_its_assigned_phase():
    raw = '{"assignments":[{"i":0,"phase":"air supply check"},{"i":1,"phase":"electrical check"}]}'
    assert parse_reassignments(raw, 2, ALLOWED) == ["air supply check", "electrical check"]


def test_keeps_other_for_a_phase_not_in_the_allowed_list():
    """A bad response must never introduce a made-up label."""
    raw = '{"assignments":[{"i":0,"phase":"made up phase"}]}'
    assert parse_reassignments(raw, 1, ALLOWED) == ["other"]


def test_keeps_other_for_indices_the_model_omitted():
    raw = '{"assignments":[{"i":0,"phase":"mechanical check"}]}'
    assert parse_reassignments(raw, 2, ALLOWED) == ["mechanical check", "other"]


def test_returns_all_other_on_malformed_output():
    assert parse_reassignments("not json", 2, ALLOWED) == ["other", "other"]


def test_falls_back_to_positional_order_when_indices_are_absent():
    raw = '{"assignments":[{"phase":"electrical check"},{"phase":"mechanical check"}]}'
    assert parse_reassignments(raw, 2, ALLOWED) == ["electrical check", "mechanical check"]


def test_trims_whitespace_around_a_phase():
    raw = '{"assignments":[{"i":0,"phase":"  electrical check  "}]}'
    assert parse_reassignments(raw, 1, ALLOWED) == ["electrical check"]


def test_handles_json_that_is_not_an_object():
    assert parse_reassignments("[1,2,3]", 1, ALLOWED) == ["other"]


# ─── consolidate_chapters ─────────────────────────────────────────────────────


def test_merges_consecutive_segments_sharing_a_phase():
    out = consolidate_chapters(
        [seg(0, 10, "diagnosis"), seg(10, 20, "diagnosis"), seg(20, 30, "repair")]
    )
    assert len(out) == 2
    assert (out[0]["start"], out[0]["end"], out[0]["mainTag"]) == (0, 20, "diagnosis")
    assert (out[1]["start"], out[1]["end"], out[1]["mainTag"]) == (20, 30, "repair")


def test_returns_empty_for_no_segments():
    assert consolidate_chapters([]) == []


def test_sorts_before_merging():
    out = consolidate_chapters([seg(20, 30, "repair"), seg(0, 10, "repair")])
    assert len(out) == 1
    assert (out[0]["start"], out[0]["end"]) == (0, 30)


def test_enforces_the_chapter_cap():
    """A choppy video must not explode the chapter count."""
    segs = [seg(i * 10, i * 10 + 10, f"phase{i}") for i in range(100)]
    assert len(consolidate_chapters(segs, max_chapters=40)) == 40


def test_capping_preserves_total_time_span():
    segs = [seg(i * 10, i * 10 + 10, f"phase{i}") for i in range(50)]
    out = consolidate_chapters(segs, max_chapters=5)
    assert out[0]["start"] == 0
    assert out[-1]["end"] == 500


def test_consolidate_does_not_mutate_its_input():
    segs = [seg(0, 10, "repair"), seg(10, 20, "repair")]
    consolidate_chapters(segs)
    assert segs[0]["end"] == 10


def test_keeps_the_first_segments_sub_tag_as_the_chapter_label():
    a, b = seg(0, 10, "repair"), seg(10, 20, "repair")
    a["subTag"] = "first"
    b["subTag"] = "second"
    assert consolidate_chapters([a, b])[0]["subTag"] == "first"


# ─── gaps ─────────────────────────────────────────────────────────────────────


def raw(start, end):
    return {"id": start, "start": start, "end": end, "text": "x"}


def test_whole_video_is_a_gap_when_nothing_was_spoken():
    assert find_unspoken_gaps([], [], 600) == [{"start": 0, "end": 600}]


def test_finds_the_gap_before_the_first_spoken_segment():
    gaps = find_unspoken_gaps([], [raw(30, 40)], 100)
    assert {"start": 0, "end": 30} in gaps


def test_finds_the_gap_after_the_last_spoken_segment():
    gaps = find_unspoken_gaps([], [raw(0, 10)], 100)
    assert {"start": 10, "end": 100} in gaps


def test_finds_a_gap_between_two_spoken_segments():
    gaps = find_unspoken_gaps([], [raw(0, 10), raw(40, 50)], 50)
    assert {"start": 10, "end": 40} in gaps


def test_ignores_gaps_shorter_than_the_minimum():
    """A 1s pause between sentences is not a silent stretch."""
    assert find_unspoken_gaps([], [raw(0, 10), raw(11, 50)], 50) == []


def test_a_silence_window_overlapping_speech_is_not_a_gap():
    gaps = find_unspoken_gaps([{"start": 5, "end": 20}], [raw(0, 30)], 30)
    assert gaps == []


def test_an_open_ended_silence_window_runs_to_the_video_end():
    gaps = find_unspoken_gaps([{"start": 40, "end": None}], [raw(0, 10)], 100)
    assert {"start": 40, "end": 100} in gaps


def test_gaps_are_sorted_by_start():
    gaps = find_unspoken_gaps([], [raw(20, 30), raw(60, 70)], 100)
    assert gaps == sorted(gaps, key=lambda g: g["start"])


def test_short_gaps_pass_through_chunking_untouched():
    assert chunk_long_gaps([{"start": 0, "end": 10}], 25) == [{"start": 0, "end": 10}]


def test_long_gaps_are_split_into_even_chunks():
    out = chunk_long_gaps([{"start": 0, "end": 60}], 25)
    assert out == [
        {"start": 0, "end": 25},
        {"start": 25, "end": 50},
        {"start": 50, "end": 60},
    ]


def test_chunking_is_capped_for_very_long_silent_videos():
    from app.pipeline.types import MAX_SILENT_CHUNKS

    out = chunk_long_gaps([{"start": 0, "end": 100_000}], 25)
    assert len(out) == MAX_SILENT_CHUNKS
