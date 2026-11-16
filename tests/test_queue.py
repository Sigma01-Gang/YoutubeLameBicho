from datetime import datetime, timedelta

import pytest

from shortsfactory.library.files import sha256_file, verify_hash
from shortsfactory.upload.runner import QueueRunner
from shortsfactory.upload.studio import SessionExpired, UploadError, UploadResult, extract_video_id


class FakeUploader:
    def __init__(self, behaviour="ok"):
        self.behaviour = behaviour
        self.calls = []

    def upload(self, req, on_stage=None):
        self.calls.append(req)
        if on_stage:
            on_stage("archivo", {"video_id": "abcdefghijk"})
        if self.behaviour == "expired":
            raise SessionExpired("caducada")
        if self.behaviour == "fail":
            raise UploadError("selector no encontrado")
        return UploadResult("abcdefghijk", "https://youtube.com/shorts/abcdefghijk")


@pytest.fixture
def setup(ctx, tmp_path):
    ch = ctx.channels.create("Canal", "es", min_gap_minutes=0)
    video = tmp_path / "v.mp4"
    video.write_bytes(b"contenido aprobado")
    return ctx, ch, video


def test_hash_helpers(tmp_path):
    f = tmp_path / "a.bin"
    f.write_bytes(b"abc")
    assert sha256_file(f) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert verify_hash(f, sha256_file(f)) and not verify_hash(f, "0" * 64)


def test_no_upload_without_approval(setup):
    ctx, ch, video = setup
    item = ctx.queue.add(ch.id, str(video), "Título")
    assert ctx.queue.get(item)["status"] == "esperando_aprobacion"
    fake = FakeUploader()
    runner = QueueRunner(ctx.db, lambda c: fake)
    assert runner.tick().outcome == "nada" and not fake.calls
    ctx.queue.approve(item, {"audio": True}, "ok")
    res = runner.tick()
    assert res.outcome == "publicado" and len(fake.calls) == 1
    it = ctx.queue.get(item)
    assert it["status"] == "publicado" and it["video_id"] == "abcdefghijk"
    assert ctx.queue.approvals(item)[0]["version"] == 1


def test_changed_file_is_blocked_by_sha(setup):
    ctx, ch, video = setup
    item = ctx.queue.add(ch.id, str(video), "T")
    ctx.queue.approve(item)
    video.write_bytes(b"otro contenido")
    ok, why = ctx.queue.is_upload_allowed(ctx.queue.get(item))
    assert not ok and "SHA-256" in why
    fake = FakeUploader()
    assert QueueRunner(ctx.db, lambda c: fake).tick().outcome == "nada" and not fake.calls


def test_interval_respected_across_restart(setup, tmp_path):
    ctx, ch, video = setup
    ctx.db.set_setting("approval_mode", False)
    ctx.db.set_setting("upload_interval_hours", 3)
    a = ctx.queue.add(ch.id, str(video), "A")
    b = ctx.queue.add(ch.id, str(video), "B")
    fake = FakeUploader()
    QueueRunner(ctx.db, lambda c: fake).tick()
    # "reinicio": nuevo contexto sobre la misma base de datos
    from shortsfactory.app_context import AppContext
    ctx2 = AppContext(ctx.db.path)
    r2 = QueueRunner(ctx2.db, lambda c: fake)
    assert r2.tick().outcome == "nada"
    assert r2.tick(now=datetime.now() + timedelta(hours=3, minutes=1)).outcome == "publicado"
    assert len(fake.calls) == 2


def test_retries_with_backoff_then_error(setup):
    ctx, ch, video = setup
    ctx.db.set_setting("approval_mode", False)
    item = ctx.queue.add(ch.id, str(video), "T")
    fake = FakeUploader("fail")
    runner = QueueRunner(ctx.db, lambda c: fake)
    now = datetime.now()
    assert runner.tick(now).outcome == "reintentando"
    it = ctx.queue.get(item)
    assert it["attempts"] == 1 and "selector" in it["last_error"] and it["upload_stage"] == "archivo"
    # antes del reintento no se vuelve a intentar
    assert runner.tick(now + timedelta(minutes=1)).outcome == "nada"
    assert runner.tick(now + timedelta(hours=4)).outcome == "reintentando"
    assert runner.tick(now + timedelta(hours=8)).outcome == "error"
    assert ctx.queue.get(item)["attempts"] == 3
    # se retoma desde el ID ya asignado por Studio
    assert fake.calls[-1].existing_video_id == "abcdefghijk"


def test_session_expired_pauses_channel(setup):
    ctx, ch, video = setup
    ctx.db.set_setting("approval_mode", False)
    item = ctx.queue.add(ch.id, str(video), "T")
    notes = []
    res = QueueRunner(ctx.db, lambda c: FakeUploader("expired"), lambda t, m: notes.append(t)).tick()
    assert res.outcome == "sesion_caducada" and ctx.channels.get(ch.id).paused
    assert ctx.queue.get(item)["status"] == "pendiente" and notes


def test_crash_recovery(setup):
    ctx, ch, video = setup
    item = ctx.queue.add(ch.id, str(video), "T")
    ctx.queue.mark_uploading(item)
    assert ctx.queue.recover_after_crash() == 1
    assert ctx.queue.get(item)["status"] == "pendiente"


def test_simulation_reorder_shift(setup):
    ctx, ch, video = setup
    ctx.db.set_setting("approval_mode", False)
    a = ctx.queue.add(ch.id, str(video), "A", publish_at="2026-12-01 18:00")
    b = ctx.queue.add(ch.id, str(video), "B", publish_at="2026-12-01 21:00")
    ctx.queue.reorder([b, a])
    plan = ctx.queue.plan()
    assert [i["id"] for i, _ in plan] == [b, a]
    fake = FakeUploader()
    res = QueueRunner(ctx.db, lambda c: fake).tick(simulate=True)
    assert "Simulación" in res.message and not fake.calls
    assert ctx.queue.shift_dates(2) == 2
    assert ctx.queue.get(a)["publish_at"] == "2026-12-01 20:00"


def test_test_mode_visibility(setup):
    ctx, ch, video = setup
    ctx.db.set_setting("approval_mode", False)
    ctx.db.set_setting("test_mode", True)
    ctx.queue.add(ch.id, str(video), "T", publish_at="2030-01-01 10:00")
    fake = FakeUploader()
    QueueRunner(ctx.db, lambda c: fake).tick()
    assert fake.calls[0].visibility == "PRIVATE" and fake.calls[0].publish_at is None


def test_extract_video_id():
    assert extract_video_id("https://youtube.com/shorts/abcdefghijk") == "abcdefghijk"
    assert extract_video_id("https://youtu.be/ABCDEFGHIJK") == "ABCDEFGHIJK"
