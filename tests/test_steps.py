"""The resume ledger.

This is what replaces the Node `workflow` package's free step durability. It is
the single most important piece of Phase 3: without it, a worker restart on a
four-hour video redoes every OpenAI call from zero, which is both slow and
expensive.

The contract: a step runs at most ONCE per (video_id, step_key). A second
attempt returns the recorded output without calling the function again.
"""

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import Base, WorkflowStep
from app.worker.steps import StepContext, chunk_key


@pytest.fixture
def engine():
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def maker(engine):
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def ctx(maker):
    with maker() as s:
        yield StepContext(s, "video-1")


class Counter:
    """Records how many times the wrapped work actually ran."""

    def __init__(self, value="result"):
        self.calls = 0
        self.value = value

    def __call__(self):
        self.calls += 1
        return self.value


# ─── basic behaviour ──────────────────────────────────────────────────────────


def test_runs_the_function_and_returns_its_result(ctx):
    assert ctx.run("prepare", Counter("hello")) == "hello"


def test_records_the_step(ctx):
    ctx.run("prepare", Counter())
    row = ctx.session.get(WorkflowStep, ("video-1", "prepare"))
    assert row is not None


def test_a_second_attempt_does_not_re_run_the_work(ctx):
    """The whole point: expensive work happens at most once."""
    fn = Counter()
    ctx.run("prepare", fn)
    ctx.run("prepare", fn)
    assert fn.calls == 1


def test_a_second_attempt_returns_the_recorded_output(ctx):
    ctx.run("prepare", Counter("first"))
    assert ctx.run("prepare", Counter("second")) == "first"


def test_distinct_step_keys_are_independent(ctx):
    a, b = Counter("a"), Counter("b")
    assert ctx.run("prepare", a) == "a"
    assert ctx.run("tag", b) == "b"
    assert (a.calls, b.calls) == (1, 1)


def test_distinct_videos_are_independent(maker):
    with maker() as s:
        one, two = Counter("one"), Counter("two")
        assert StepContext(s, "video-1").run("prepare", one) == "one"
        assert StepContext(s, "video-2").run("prepare", two) == "two"
        assert (one.calls, two.calls) == (1, 1)


# ─── crash resume ─────────────────────────────────────────────────────────────


def test_resumes_in_a_fresh_process_without_redoing_work(maker):
    """Simulates a worker restart: new session, same ledger.

    A worker killed mid-video must pick up where it left off rather than
    replaying every completed step.
    """
    first = Counter("prepared")
    with maker() as s:
        StepContext(s, "video-1").run("prepare", first)

    second = Counter("prepared-again")
    with maker() as s:  # a brand new session, as a restarted worker would have
        result = StepContext(s, "video-1").run("prepare", second)

    assert result == "prepared"
    assert second.calls == 0


def test_a_partially_complete_video_only_runs_the_remaining_steps(maker):
    """The realistic case: killed after step 2 of 4."""
    ran: list[str] = []

    def pipeline(session):
        ctx = StepContext(session, "video-1")
        for name in ("prepare", "transcribe", "tag", "save"):
            ctx.run(name, lambda n=name: (ran.append(n), n)[1])

    with maker() as s:
        ctx = StepContext(s, "video-1")
        ctx.run("prepare", lambda: (ran.append("prepare"), "prepare")[1])
        ctx.run("transcribe", lambda: (ran.append("transcribe"), "transcribe")[1])

    assert ran == ["prepare", "transcribe"]

    ran.clear()
    with maker() as s:  # restart
        pipeline(s)

    # Only the steps that had not completed run again.
    assert ran == ["tag", "save"]


def test_fan_out_slices_resume_independently(maker):
    """A worker killed at minute 90 of a 4hr video resumes at minute 90.

    Each audio slice carries its offset in the key, so completed slices are
    skipped and only the unfinished ones re-run.
    """
    offsets = [0, 600, 1200, 1800]
    ran: list[int] = []

    with maker() as s:
        ctx = StepContext(s, "video-1")
        for off in offsets[:2]:  # first two complete, then the worker dies
            ctx.run(chunk_key("transcribe", off), lambda o=off: (ran.append(o), o)[1])

    assert ran == [0, 600]

    ran.clear()
    with maker() as s:  # restart
        ctx = StepContext(s, "video-1")
        results = [
            ctx.run(chunk_key("transcribe", off), lambda o=off: (ran.append(o), o)[1])
            for off in offsets
        ]

    assert ran == [1200, 1800]  # only the unfinished slices
    assert results == [0, 600, 1200, 1800]  # but every result is available


def test_chunk_keys_are_distinct_per_offset():
    assert chunk_key("transcribe", 0) != chunk_key("transcribe", 600)
    assert chunk_key("transcribe", 600) == "transcribe:600"


def test_chunk_keys_round_fractional_offsets():
    """Float drift must not produce two keys for the same slice."""
    assert chunk_key("transcribe", 600.0) == chunk_key("transcribe", 600.4)


# ─── failure behaviour ────────────────────────────────────────────────────────


def test_a_failing_step_is_not_recorded(ctx):
    """A failed step MUST re-run on retry — recording it would skip it forever."""

    def boom():
        raise RuntimeError("step exploded")

    with pytest.raises(RuntimeError):
        ctx.run("prepare", boom)

    assert ctx.session.get(WorkflowStep, ("video-1", "prepare")) is None


def test_a_failing_step_propagates(ctx):
    def boom():
        raise ValueError("nope")

    with pytest.raises(ValueError, match="nope"):
        ctx.run("prepare", boom)


def test_a_step_that_failed_then_succeeds_is_recorded(ctx):
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient")
        return "recovered"

    with pytest.raises(RuntimeError):
        ctx.run("prepare", flaky)
    assert ctx.run("prepare", flaky) == "recovered"
    assert calls["n"] == 2


# ─── payload round-tripping ───────────────────────────────────────────────────


def test_none_round_trips_and_still_counts_as_complete(ctx):
    """A step returning nothing must not re-run. Storing a bare JSON null
    would be indistinguishable from 'no row', so the value is enveloped."""
    fn = Counter(None)
    assert ctx.run("save", fn) is None
    assert ctx.run("save", fn) is None
    assert fn.calls == 1


@pytest.mark.parametrize(
    "value",
    [
        "a string",
        42,
        3.14,
        True,
        False,
        [],
        {},
        [1, 2, 3],
        {"a": 1, "b": [2, 3]},
        [{"start": 0.0, "end": 1.5, "text": "hi", "mainTag": "intro"}],
    ],
)
def test_json_values_round_trip_unchanged(ctx, value):
    ctx.run("k", lambda: value)
    with_cache = ctx.run("k", lambda: "SHOULD NOT RUN")
    assert with_cache == value


def test_a_realistic_pipeline_payload_survives(ctx):
    payload = {
        "key": "videos/a.mp4",
        "container": "videosvc",
        "duration": 900.5,
        "segments": [{"offset": 0, "dur": 600}, {"offset": 600, "dur": 300.5}],
    }
    ctx.run("prepare", lambda: payload)
    assert ctx.run("prepare", lambda: None) == payload


def test_tuples_come_back_as_lists(ctx):
    """JSON has no tuple. Callers must not rely on the distinction — this is
    documented here rather than discovered in production."""
    ctx.run("k", lambda: (1, 2))
    assert ctx.run("k", lambda: None) == [1, 2]


# ─── introspection ────────────────────────────────────────────────────────────


def test_completed_reports_which_steps_are_done(ctx):
    ctx.run("prepare", Counter())
    ctx.run("tag", Counter())
    assert ctx.completed() == {"prepare", "tag"}


def test_clear_removes_the_ledger_for_one_video(maker):
    """Reprocessing a video must start clean, not resume a stale run."""
    with maker() as s:
        StepContext(s, "video-1").run("prepare", Counter())
        StepContext(s, "video-2").run("prepare", Counter())

        StepContext(s, "video-1").clear()

        remaining = s.scalars(select(WorkflowStep)).all()
        assert [r.video_id for r in remaining] == ["video-2"]


def test_clearing_makes_a_step_run_again(ctx):
    fn = Counter()
    ctx.run("prepare", fn)
    ctx.clear()
    ctx.run("prepare", fn)
    assert fn.calls == 2


def test_the_session_is_exposed_for_the_pipeline_to_use(ctx):
    assert isinstance(ctx.session, Session)
