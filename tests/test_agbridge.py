import json
import subprocess

import pytest

from vh import agbridge, discovery, gemini, library, pipeline


def _proc(stdout, code=0):
    return subprocess.CompletedProcess([], code, stdout=stdout, stderr="")


def test_agentapi_tries_routes_and_caches(monkeypatch):
    calls = []

    def fake_run(cmd, env=None, **kw):
        calls.append((env["ANTIGRAVITY_LS_ADDRESS"], env.get("ANTIGRAVITY_PROJECT_ID", "")))
        if env["ANTIGRAVITY_LS_ADDRESS"].endswith(":2") and env.get("ANTIGRAVITY_PROJECT_ID") == "outside-of-project":
            return _proc(json.dumps({"response": {"newConversation": {"conversationId": "cid-1"}}}))
        return _proc(json.dumps({"response": {}, "error": "connection error"}), 1)

    monkeypatch.setattr(agbridge, "_route", None)
    monkeypatch.setattr(subprocess, "run", fake_run)
    servers = [{"exe": "ls.exe", "csrf": "t", "ports": [1, 2], "pid": 1}]
    monkeypatch.setattr(agbridge, "scan", lambda: servers)
    assert agbridge.new_conversation("hi", "title") == "cid-1"
    assert calls[-1] == ("localhost:2", "outside-of-project")
    assert agbridge._route[2] == 2
    calls.clear()
    agbridge.new_conversation("again", "title")
    assert calls == [("localhost:2", "outside-of-project")]  # сразу кэшированный маршрут


def test_run_task_reads_result_file(monkeypatch, tmp_path):
    monkeypatch.setattr(agbridge, "ensure_running", lambda progress=None: [{"ports": [1]}])

    def fake_new(prompt, title, model=""):
        path = prompt.split("write the answer to ")[1].split(" using")[0]
        with open(path, "w", encoding="utf-8") as fh:
            fh.write('```json\n{"moments": [{"title": "Сцена"}]}\n```')
        return "cid"

    monkeypatch.setattr(agbridge, "new_conversation", fake_new)
    monkeypatch.setattr(agbridge, "brain_dir", lambda: tmp_path)
    res = agbridge.run_task("moments", "задание", timeout=10)
    assert res == {"moments": [{"title": "Сцена"}]}


def test_wait_result_uses_transcript_answer(monkeypatch, tmp_path):
    monkeypatch.setattr(agbridge, "brain_dir", lambda: tmp_path)
    t = tmp_path / "cid" / ".system_generated" / "logs"
    t.mkdir(parents=True)
    (t / "transcript.jsonl").write_text(json.dumps({"step_index": 1, "type": "PLANNER_RESPONSE", "tool_calls": [
        {"name": "write_to_file", "args": {"TargetFile": r"C:\x\result.json", "CodeContent": '{"ok": true}'}}]}) + "\n",
        encoding="utf-8")
    assert agbridge.wait_result("cid", tmp_path / "result.json", timeout=10) == {"ok": True}


def test_gemini_prefers_antigravity_over_api(monkeypatch):
    monkeypatch.setattr(gemini, "_key", lambda: "AIzaTEST")
    monkeypatch.setattr(agbridge, "available", lambda: True)
    monkeypatch.setattr(agbridge, "run_task", lambda *a, **k: {"moments": [{"title": "A", "mood": "epic"}]})
    monkeypatch.setattr(gemini, "_api_call", lambda *a, **k: pytest.fail("API не должен вызываться"))
    assert gemini.status()["channel"] == "antigravity"
    assert gemini.suggest_moments("Test", [])[0]["title"] == "A"


def test_gemini_falls_back_to_api(monkeypatch):
    monkeypatch.setattr(gemini, "_key", lambda: "AIzaTEST")
    monkeypatch.setattr(agbridge, "available", lambda: True)

    def boom(*a, **k):
        raise agbridge.AntigravityError("нет ответа")

    monkeypatch.setattr(agbridge, "run_task", boom)
    monkeypatch.setattr(gemini, "_api_call", lambda *a, **k: '{"moments": [{"title": "B"}]}')
    assert gemini.suggest_moments("Test", [])[0]["title"] == "B"


def test_source_queries_fall_back_to_anime_title():
    qs = discovery.source_queries({"title": "Гон против Питу", "query": "гон против питу",
                                   "query_en": "gon vs pitou", "anime_en": "Hunter x Hunter", "anime_ru": "Хантер х Хантер"})
    assert qs[0] == "gon vs pitou"
    assert any("Hunter x Hunter" in q for q in qs)
    assert len(qs) == len({q.lower() for q in qs})


def test_fetch_source_tries_next_candidate(monkeypatch, sample_video):
    clip = library.add_clip({"anime": "Тест", "title": "Сцена", "query": "q"})
    monkeypatch.setattr(discovery, "find_source_for_moment", lambda *a, **k: [
        {"url": "https://youtu.be/dead", "title": "dead", "views": 9}, {"url": "https://youtu.be/ok", "title": "ok", "views": 1}])

    def info(url):
        if url.endswith("dead"):
            raise RuntimeError("Video unavailable")
        return {"url": url, "duration": 20, "heatmap": [], "views": 1}

    monkeypatch.setattr(discovery, "video_info", info)
    monkeypatch.setattr(discovery, "download", lambda url, **k: {
        "path": str(sample_video), "duration": 20.0, "width": 1280, "height": 720, "offset": 0.0,
        "info": {"id": "ok", "title": "ok", "channel": "c", "views": 1, "url": url, "heatmap": []}})
    monkeypatch.setattr(pipeline, "TARGET_SECONDS", 6)
    clip = pipeline.fetch_source(clip["id"])
    assert clip["source_url"] == "https://youtu.be/ok" and clip["stage"] == "downloaded"


def test_missing_ytdlp_is_reported(monkeypatch):
    def no_ytdlp():
        raise discovery.SourceError("Не установлен yt-dlp")

    monkeypatch.setattr(discovery, "_import_ytdlp", no_ytdlp)
    with pytest.raises(discovery.SourceError):
        discovery.find_source_for_moment({"query": "x"})
