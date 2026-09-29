import json
import subprocess

from vh import agbridge, library, moments
from vh.scenes import plan_from_beats, protect_speech


def _b(s, e, role="build", imp=5, inten=5, speech=False, what=""):
    return {"start": s, "end": e, "role": role, "importance": imp, "intensity": inten, "speech": speech, "what": what}


def test_plan_keeps_everything_when_it_fits():
    beats = [_b(0, 3, "intro_outro"), _b(3, 15, "setup", speech=True), _b(15, 30, "climax", inten=9), _b(30, 40, "payoff")]
    plan = plan_from_beats(beats, 40, peak=20, min_len=20, max_len=58)
    assert plan["parts"] == [[3.0, 40.0]]            # заставка убрана, остальное — целиком


def test_plan_trims_edges_first_and_never_cuts_speech_or_climax():
    beats = [_b(0, 10, "filler", imp=1, inten=1), _b(10, 25, "setup", speech=True, imp=8),
             _b(25, 30, "filler", imp=1, inten=1), _b(30, 50, "build", speech=True), _b(50, 62, "climax", imp=9, inten=10),
             _b(62, 70, "reaction", imp=6), _b(70, 90, "filler", imp=2, inten=2)]
    plan = plan_from_beats(beats, 90, peak=55, min_len=35, max_len=58)
    kept = plan["parts"]
    total = sum(e - s for s, e in kept)
    assert total <= 58.01
    assert any(s <= 50 and e >= 62 for s, e in kept), "кульминация целиком"
    assert any(s <= 30 and e >= 50 for s, e in kept), "реплики в build не вырезаны"
    # пустой проход 25–30 без речи можно убрать, но реплика 10–25 должна остаться, если влезает
    assert kept[0][0] >= 10 - 1e-6


def test_plan_merges_tiny_gaps():
    beats = [_b(0, 20, "setup", speech=True), _b(20, 21, "filler", imp=0, inten=0), _b(21, 40, "climax", imp=9)]
    plan = plan_from_beats(beats, 40, peak=30, min_len=10, max_len=39.5)
    assert len(plan["parts"]) == 1                    # разрыв короче 1.2 c не делаем — это режет глаз


def test_protect_speech_extends_cut_to_phrase_end():
    parts = [[0, 10], [20, 30]]
    subs = [{"start": 8, "end": 12.5, "text": "фраза через склейку"}, {"start": 19, "end": 21, "text": "вторая"}]
    out = protect_speech(parts, subs, duration=40)
    assert out[0][1] >= 12.5 and out[1][0] <= 19


def _proc(stdout, code=0):
    return subprocess.CompletedProcess([], code, stdout=stdout, stderr="")


def test_clip_chat_reused_and_deleted_after_publish(monkeypatch, tmp_path):
    calls = []
    servers = [{"exe": "ls.exe", "csrf": "t", "ports": [2], "pid": 1}]
    monkeypatch.setattr(agbridge, "scan", lambda: servers)
    monkeypatch.setattr(agbridge, "ensure_running", lambda progress=None: servers)
    monkeypatch.setattr(agbridge, "_route", None)
    monkeypatch.setattr(agbridge, "brain_dir", lambda: tmp_path)

    def fake_run(cmd, env=None, **kw):
        args = cmd[2:]
        calls.append(args[0])
        if args[0] == "new-conversation":
            prompt = args[-1]
        else:
            prompt = args[-1]
        path = prompt.split("write the answer to ")[1].split(" using")[0]
        with open(path, "w", encoding="utf-8") as fh:
            fh.write('{"ok": true}')
        resp = {"newConversation": {"conversationId": "cid-clip"}} if args[0] == "new-conversation" else {}
        return _proc(json.dumps({"response": resp}))

    deleted = []
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(agbridge, "_rpc", lambda method, body, timeout=15: deleted.append((method, body)) or {})

    clip = library.add_clip({"anime": "Тест", "title": "Сцена"})
    chat = library.clip_chat(clip)
    assert agbridge.run_task("analyze", "x", chat=chat) == {"ok": True}
    library.save_chat(clip["id"], chat)
    chat2 = library.clip_chat(library.require_clip(clip["id"]))
    assert chat2["id"] == "cid-clip"
    agbridge.run_task("captions", "y", chat=chat2)
    assert calls == ["new-conversation", "send-message"]        # 1 клип = 1 чат
    assert deleted == []                                        # чат клипа живёт до публикации

    import threading
    before = set(threading.enumerate())
    library.advance_stage(clip["id"], "published")
    for th in set(threading.enumerate()) - before:
        th.join(timeout=5)
    assert ("DeleteCascadeTrajectory", {"cascadeId": "cid-clip"}) in deleted


def test_oneshot_chat_deleted_immediately(monkeypatch, tmp_path):
    servers = [{"exe": "ls.exe", "csrf": "t", "ports": [2], "pid": 1}]
    monkeypatch.setattr(agbridge, "ensure_running", lambda progress=None: servers)
    monkeypatch.setattr(agbridge, "brain_dir", lambda: tmp_path)

    def fake_new(prompt, title, model=""):
        path = prompt.split("write the answer to ")[1].split(" using")[0]
        with open(path, "w", encoding="utf-8") as fh:
            fh.write('{"scores": []}')
        return "cid-once"

    deleted = []
    monkeypatch.setattr(agbridge, "new_conversation", fake_new)
    monkeypatch.setattr(agbridge, "_rpc", lambda method, body, timeout=15: deleted.append(body["cascadeId"]) or {})
    agbridge.run_task("rate", "z")
    assert deleted == ["cid-once"]


def test_scores_saved_and_used_for_sorting(monkeypatch):
    from vh import gemini, pipeline

    key = "one piece"
    ms = moments.list_moments(key)
    monkeypatch.setattr(gemini, "rate_moments", lambda *a, **k: {ms[-1]["id"]: {"score": 91, "why": "культовая"},
                                                                  ms[0]["id"]: {"score": 20, "why": "слабая"}})
    res = moments.rate_anime(key)
    assert res["rated"] == 2
    top = {m["id"]: m for m in moments.list_moments(key)}
    assert top[ms[-1]["id"]]["score"] == 91 and top[ms[-1]["id"]]["score_why"] == "культовая"
    picked = pipeline.pick_fresh_moments(1, [key])
    assert picked[0]["id"] == ms[-1]["id"]
