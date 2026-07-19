"""The resume ledger.

Replaces the durability the Node `workflow` package provided for free. Node
marked a function `"use step"` and the engine guaranteed it ran once, surviving
pod restarts. Python has no equivalent, so we record each completed step's
output in the `workflow_step` table and skip it on a retry.

Usage:

    ctx = StepContext(session, video_id)
    prep = ctx.run("prepare", lambda: prepare(video_id))
    for offset in offsets:
        ctx.run(chunk_key("transcribe", offset), lambda: transcribe(offset))

What this buys: a worker killed at minute 90 of a four-hour video resumes at
minute 90 instead of replaying every completed OpenAI call — which matters for
both wall-clock and spend.

What it does NOT do: run steps in parallel across workers, or resume a step that
was interrupted midway. A step is atomic — interrupted means not done, so it
runs again from the top.
"""

import logging
from collections.abc import Callable
from typing import Any, cast

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import WorkflowStep

log = logging.getLogger(__name__)


def chunk_key(name: str, offset: float) -> str:
    """Key for one slice of a fan-out step, e.g. `transcribe:600`.

    The offset is rounded so float drift cannot produce two keys for the same
    slice — which would silently re-run work the ledger thinks is done.
    """
    return f"{name}:{round(offset)}"


class StepContext:
    """Runs steps at most once per (video_id, step_key)."""

    def __init__(self, session: Session, video_id: str):
        self.session = session
        self.video_id = video_id

    def run(self, step_key: str, fn: Callable[[], Any]) -> Any:
        """Return this step's output, running `fn` only if it has not completed.

        A raising `fn` is NOT recorded, so the step re-runs on the next attempt.
        Recording a failure would skip it forever and silently produce a video
        missing that stage's output.
        """
        row = self.session.get(WorkflowStep, (self.video_id, step_key))
        if row is not None:
            log.info("[step] %s/%s already done — skipping", self.video_id, step_key)
            # The envelope is always a dict; WorkflowStep.output is typed
            # dict|list because the column accepts either.
            return cast(dict, row.output).get("v")

        result = fn()

        # Enveloped as {"v": ...} so any JSON value round-trips unambiguously.
        # A bare null would be indistinguishable from "no row recorded", and a
        # step that legitimately returns nothing would re-run forever.
        self.session.add(
            WorkflowStep(video_id=self.video_id, step_key=step_key, output={"v": result})
        )
        self.session.commit()
        return result

    def completed(self) -> set[str]:
        """The step keys already recorded for this video."""
        rows = self.session.scalars(
            select(WorkflowStep.step_key).where(WorkflowStep.video_id == self.video_id)
        )
        return set(rows)

    def clear(self) -> None:
        """Drop this video's ledger so a reprocess starts clean.

        Without this, reprocessing would resume a stale run and return the old
        results instead of recomputing them.
        """
        self.session.execute(delete(WorkflowStep).where(WorkflowStep.video_id == self.video_id))
        self.session.commit()
