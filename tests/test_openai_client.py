"""Model selection, fallback and usage recording.

Ported from youtube-clone/src/lib/pipeline/openai.ts.

Models are PINNED in code, not read from the environment. That is deliberate:
the Azure ConfigMap sets OPENAI_MODEL=gpt-4o, and an env var beats a code
default — so a default alone would silently keep the deployment on gpt-4o.

The fallback is a safety net but also a trap when comparing models: a key
without access to the pinned model would silently produce gpt-4o output that
looks like success. So a fallback is recorded and surfaced on /api/health.
"""

import pytest

from app.pipeline import openai_client as oc
from app.pipeline.usage import reset_usage, usage_snapshot


class FakeResponse:
    def __init__(self, model, usage=None):
        self.model = model
        self.usage = usage


class FakeClient:
    """Records the models it was asked for; optionally fails on some of them."""

    def __init__(self, fail_for=(), error=None):
        self.fail_for = set(fail_for)
        self.error = error or ModelUnavailable()
        self.calls = []
        self.chat = self

    @property
    def completions(self):
        return self

    def create(self, **kwargs):
        model = kwargs["model"]
        self.calls.append(model)
        if model in self.fail_for:
            raise self.error
        return FakeResponse(model, usage={"prompt_tokens": 10, "completion_tokens": 5})


class ModelUnavailable(Exception):
    status = 404
    code = "model_not_found"


class Transient(Exception):
    status = 500


@pytest.fixture(autouse=True)
def _reset():
    reset_usage()
    oc.reset_fallback_state()
    yield
    reset_usage()
    oc.reset_fallback_state()


@pytest.fixture
def client(monkeypatch):
    def install(fake):
        monkeypatch.setattr(oc, "get_client", lambda: fake)
        return fake

    return install


def call(**kw):
    return oc.chat_complete(messages=[{"role": "user", "content": "hi"}], **kw)


# ─── model selection ──────────────────────────────────────────────────────────


def test_default_calls_use_the_flagship(client):
    fake = client(FakeClient())
    call()
    assert fake.calls == ["gpt-5.4"]


def test_mini_calls_use_the_mini_tier(client):
    fake = client(FakeClient())
    call(mini=True)
    assert fake.calls == ["gpt-5.4-mini"]


def test_vision_calls_use_the_mini_tier_not_the_flagship(client):
    """The flagship REJECTS image_url content; the mini variant accepts it.

    Verified by A/B probe against the live API. Routing vision to the flagship
    is the bug this pins down.
    """
    fake = client(FakeClient())
    call(vision=True)
    assert fake.calls == ["gpt-5.4-mini"]


# ─── fallback ─────────────────────────────────────────────────────────────────


def test_falls_back_when_the_key_cannot_use_the_pinned_model(client):
    fake = client(FakeClient(fail_for=["gpt-5.4"]))
    res = call()
    assert fake.calls == ["gpt-5.4", "gpt-4o"]
    assert res.model == "gpt-4o"


def test_mini_falls_back_to_the_mini_fallback(client):
    fake = client(FakeClient(fail_for=["gpt-5.4-mini"]))
    call(mini=True)
    assert fake.calls == ["gpt-5.4-mini", "gpt-4o-mini"]


def test_vision_falls_back_to_a_vision_capable_model(client):
    fake = client(FakeClient(fail_for=["gpt-5.4-mini"]))
    call(vision=True)
    assert fake.calls == ["gpt-5.4-mini", "gpt-4o"]


@pytest.mark.parametrize(
    "err",
    [
        type("E", (Exception,), {"status": 404})(),
        type("E", (Exception,), {"status": 403})(),
        type("E", (Exception,), {"code": "model_not_found"})(),
        type("E", (Exception,), {"message": "The model does not exist"})(),
        type("E", (Exception,), {"message": "You do not have access to this model"})(),
        type("E", (Exception,), {"message": "model not available"})(),
    ],
)
def test_all_model_unavailable_shapes_trigger_fallback(client, err):
    fake = client(FakeClient(fail_for=["gpt-5.4"], error=err))
    call()
    assert fake.calls == ["gpt-5.4", "gpt-4o"]


def test_a_transient_error_is_not_retried_with_a_different_model(client):
    """A 500 means try again later, not "use a lesser model"."""
    fake = client(FakeClient(fail_for=["gpt-5.4"], error=Transient()))
    with pytest.raises(Transient):
        call()
    assert fake.calls == ["gpt-5.4"]


def test_a_fallback_is_recorded_so_it_is_never_silent(client):
    client(FakeClient(fail_for=["gpt-5.4"]))
    assert oc.active_models()["fellBackTo"] is None
    call()
    assert oc.active_models()["fellBackTo"] == "gpt-4o"


def test_no_fallback_leaves_the_health_report_clean(client):
    client(FakeClient())
    call()
    assert oc.active_models()["fellBackTo"] is None


def test_a_failing_fallback_propagates(client):
    client(FakeClient(fail_for=["gpt-5.4", "gpt-4o"]))
    with pytest.raises(ModelUnavailable):
        call()


# ─── usage recording ──────────────────────────────────────────────────────────


def test_usage_is_recorded_for_every_call(client):
    client(FakeClient())
    call()
    assert usage_snapshot().chat_calls == 1
    assert usage_snapshot().prompt_tokens == 10


def test_usage_is_priced_against_the_model_that_actually_ran(client):
    """Pricing the requested model instead of the served one misreports cost."""
    client(FakeClient(fail_for=["gpt-5.4"]))
    call()
    # gpt-4o: $2.50/1M in, $10.00/1M out -> 10 in + 5 out
    assert usage_snapshot().usd == pytest.approx(10 / 1e6 * 2.5 + 5 / 1e6 * 10.0)


# ─── health report ────────────────────────────────────────────────────────────


def test_active_models_reports_the_pinned_models():
    m = oc.active_models()
    assert m["model"] == "gpt-5.4"
    assert m["modelMini"] == "gpt-5.4-mini"
    assert m["visionModel"] == "gpt-5.4-mini"
    assert m["transcription"] == "whisper-1"
