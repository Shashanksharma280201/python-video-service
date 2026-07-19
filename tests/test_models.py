"""Model behaviour that the service depends on.

The Video table mirrors youtube-clone/prisma/schema.prisma. WorkflowStep is new
— it is what buys back the crash-resume the Node `workflow` package gave free.
"""

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Base, TranscriptStatus, Video, WorkflowStep


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def make_video(**kw) -> Video:
    return Video(
        title=kw.pop("title", "clip.mp4"), blob_url=kw.pop("blob_url", "https://x/y"), **kw
    )


# ─── Video ────────────────────────────────────────────────────────────────────


def test_a_new_video_gets_an_id_without_asking_the_database(session):
    """The route needs video.id to enqueue work before it commits."""
    v = make_video()
    assert v.id
    assert isinstance(v.id, str)


def test_ids_are_unique(session):
    assert make_video().id != make_video().id


def test_defaults_match_the_prisma_schema(session):
    v = make_video()
    session.add(v)
    session.commit()
    assert v.description == ""
    assert v.views == 0
    assert v.transcript_status == TranscriptStatus.NONE
    assert v.external_id is None
    assert v.machine_id is None
    assert v.tenant_id is None
    assert v.transcript_segments is None
    assert v.topic_segments is None
    assert v.domain_data is None
    assert v.thumbnail_url is None
    assert v.created_at is not None


def test_external_id_is_unique(session):
    """This constraint is what makes ingest idempotent under a race."""
    session.add(make_video(external_id="r-1"))
    session.commit()
    session.add(make_video(external_id="r-1"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_external_id_allows_many_nulls(session):
    """Uploads have no external id until an extraction claims them."""
    session.add_all([make_video(), make_video()])
    session.commit()
    assert len(session.scalars(select(Video)).all()) == 2


def test_json_columns_round_trip_structured_data(session):
    v = make_video(
        transcript_segments=[{"start": 0.0, "end": 1.5, "text": "hi"}],
        topic_segments=[{"mainTag": "intro", "start": 0.0, "end": 1.5}],
        domain_data={"machine": "Lube Pump", "troubleshooting": []},
    )
    session.add(v)
    session.commit()
    session.expire_all()

    got = session.get(Video, v.id)
    assert got.transcript_segments[0]["text"] == "hi"
    assert got.topic_segments[0]["mainTag"] == "intro"
    assert got.domain_data["machine"] == "Lube Pump"


def test_status_values_match_the_wire_strings(session):
    """The client reads these strings verbatim — they cannot drift."""
    assert [s.value for s in TranscriptStatus] == [
        "NONE",
        "PENDING",
        "PROCESSING",
        "DONE",
        "FAILED",
    ]


def test_lookup_by_external_id(session):
    session.add(make_video(external_id="azure-newvid-001"))
    session.commit()
    found = session.scalar(select(Video).where(Video.external_id == "azure-newvid-001"))
    assert found is not None


# ─── WorkflowStep ─────────────────────────────────────────────────────────────


def test_a_step_records_its_output(session):
    session.add(WorkflowStep(video_id="v1", step_key="prepare", output={"duration": 900.0}))
    session.commit()
    got = session.get(WorkflowStep, ("v1", "prepare"))
    assert got.output["duration"] == 900.0


def test_the_same_step_cannot_be_recorded_twice_for_one_video(session):
    """Composite PK is what makes resume idempotent."""
    session.add(WorkflowStep(video_id="v1", step_key="prepare", output={}))
    session.commit()
    session.add(WorkflowStep(video_id="v1", step_key="prepare", output={}))
    with pytest.raises(IntegrityError):
        session.commit()


def test_fan_out_steps_are_distinguished_by_key(session):
    """transcribe:0 and transcribe:600 are separate resumable units."""
    session.add_all(
        [
            WorkflowStep(video_id="v1", step_key="transcribe:0", output={"n": 1}),
            WorkflowStep(video_id="v1", step_key="transcribe:600", output={"n": 2}),
        ]
    )
    session.commit()
    steps = session.scalars(select(WorkflowStep).where(WorkflowStep.video_id == "v1")).all()
    assert len(steps) == 2


def test_different_videos_may_share_a_step_key(session):
    session.add_all(
        [
            WorkflowStep(video_id="v1", step_key="prepare", output={}),
            WorkflowStep(video_id="v2", step_key="prepare", output={}),
        ]
    )
    session.commit()
    assert len(session.scalars(select(WorkflowStep)).all()) == 2
