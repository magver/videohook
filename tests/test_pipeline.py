import json

from vh import antigravity, discovery, library, moments, pipeline, publish
from vh.core import probe, rel_to_work, resolve_work


def test_full_offline_pipeline(sample_video, monkeypatch):
    monkeypatch.setattr(moments, "ensure_fresh", lambda *a, **k: None)
    key = "demon slayer"
    m = moments.list_moments(key, include_used=False)[0]
    clip = pipeline.create_from_moment(key, m["id"])
    assert clip["stage"] == "found" and "ufotable" in clip["credit"]
    assert moments.usage_of(m["id"])["count"] == 1

    # источник: вместо YouTube — локальный файл
    def fake_download(url, section=None, name_hint="", progress=None):
        return {"path": str(sample_video), "duration": 20.0, "width": 1280, "height": 720, "offset": 0.0,
                "info": {"id": "x", "title": "sample", "channel": "ch", "views": 1000, "url": url, "heatmap": []}}
    monkeypatch.setattr(discovery, "find_source_for_moment", lambda *a, **k: [{"url": "https://youtu.be/x", "views": 1}])
    monkeypatch.setattr(discovery, "video_info", lambda url: {"url": url, "duration": 20, "heatmap": [], "views": 1000})
    monkeypatch.setattr(discovery, "download", fake_download)
    monkeypatch.setattr(pipeline, "TARGET_SECONDS", 6)
    clip = pipeline.fetch_source(clip["id"])
    assert clip["stage"] == "downloaded"
    assert clip["suggestions"], "должны быть подсказки отрезков"
    seg = clip["segment"]
    assert seg["start"] <= 8 and seg["end"] >= 12, f"громкий участок 8–12 c должен войти целиком, получено {seg}"
    assert seg["end"] - seg["start"] >= 15, "отрезок расширяется до законченной сцены, а не режется по окну"

    clip = pipeline.make_render(clip["id"], {"template": "cinema", "music": "epic_orchestral"})
    assert clip["stage"] == "rendered"
    out = resolve_work(clip["render"]["file"])
    info = probe(out)
    assert (info["width"], info["height"]) == (1080, 1920)
    assert info["has_audio"]

    # Antigravity: без agentapi → ручной режим
    monkeypatch.setattr(antigravity, "find_agentapi", lambda: None)
    res = pipeline.send_to_antigravity(clip["id"])
    assert res["mode"] == "manual" and "BRIEF.md" in res["prompt"]
    clip = library.require_clip(clip["id"])
    job_dir = resolve_work(clip["ag"]["dir"])
    for f in ("BRIEF.md", "job.json", "input/source_cut.mp4", "input/draft_9x16.mp4", "input/storyboard.jpg"):
        assert (job_dir / f).exists(), f
    brief = (job_dir / "BRIEF.md").read_text(encoding="utf-8")
    assert "output/result.json" in brief and "ufotable" in brief

    # имитация работы агента
    out_dir = job_dir / "output"
    (out_dir / "status.txt").write_text("Монтирую хук\n", encoding="utf-8")
    assert antigravity.check_job(clip)["ag"]["message"] == "Монтирую хук"
    import shutil
    shutil.copy2(job_dir / "input" / "draft_9x16.mp4", out_dir / "final.mp4")
    (out_dir / "result.json").write_text(json.dumps({"status": "done", "captions": {
        "youtube": {"title": "Тест #shorts", "description": "d", "tags": ["a"]},
        "tiktok": {"caption": "t"}, "instagram": {"caption": "i"}}, "edits": ["зум"]}, ensure_ascii=False), encoding="utf-8")
    clip = antigravity.check_job(library.require_clip(clip["id"]))
    assert clip["stage"] == "ready"
    assert resolve_work(clip["final_file"]).exists()
    assert clip["publish"]["captions"]["youtube"]["title"] == "Тест #shorts"

    publish.mark_published(clip["id"], "youtube", "https://youtube.com/shorts/x")
    assert library.require_clip(clip["id"])["stage"] == "published"


def test_skip_antigravity_and_offline_captions(sample_video, monkeypatch):
    clip = library.add_clip({"anime": "Тест", "title": "Сцена", "hook": "Хук", "source_file": rel_to_work(sample_video),
                             "segment": {"start": 1, "end": 6}, "credit": "© test"})
    pipeline.make_render(clip["id"], {"template": "fullscreen", "music": "none"})
    antigravity.use_draft_as_final(clip["id"])
    monkeypatch.setattr("vh.gemini.available", lambda: False)
    caps = publish.prepare_captions(clip["id"])
    assert caps["source"] == "template"
    assert library.require_clip(clip["id"])["stage"] == "ready"


def test_refine_bounds_snaps_to_pauses_and_keeps_ending(tmp_path):
    from vh.core import run_ffmpeg
    from vh.discovery import refine_bounds

    # «речь»: фразы 2–9 c и 11–19.5 c, паузы 0–2, 9–11, 19.5–24
    src = tmp_path / "speech.mp4"
    vol = "if(between(t,2,9)+between(t,11,19.5),1,0.005)"
    run_ffmpeg(["-f", "lavfi", "-i", "color=c=gray:s=320x180:r=30:d=24", "-f", "lavfi", "-i", "sine=f=300:d=24",
                "-filter_complex", f"[1:a]volume='{vol}':eval=frame[a]", "-map", "0:v", "-map", "[a]",
                "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(src)])
    # окно режет первую фразу в начале и вторую — в конце
    r = refine_bounds(src, 4.0, 16.0, min_len=5, max_len=30)
    assert r["start"] <= 2.1, r      # к паузе перед фразой
    assert r["end"] >= 19.5, r       # фраза договорена до конца
    assert r["end"] <= 24.0


def test_timeline_maps_slowmo_and_transitions():
    from vh.render import Timeline, map_items

    tl = Timeline([(0, 10), (20, 30)], slowmo={"t": 5, "dur": 2, "factor": 0.5}, xfade=0.35)
    assert abs(tl.seg_durs[0] - 12.0) < 1e-6          # 2 c замедления → 4 c
    assert abs(tl.duration - (12 + 10 - 0.35)) < 1e-6
    assert tl.map(4.0) == 4.0 and tl.map(5.0) == 6.0 and tl.map(6.0) == 8.0
    assert abs(tl.map(20.0) - (12 - 0.35)) < 1e-6
    assert tl.map(15.0) is None                       # вырезанная середина
    subs = map_items(tl, [{"start": 21, "end": 23, "text": "x"}, {"start": 14, "end": 16, "text": "cut"}])
    assert len(subs) == 1 and subs[0]["text"] == "x"


def test_settings_migrate_old_defaults(monkeypatch):
    from vh import core

    data = {"default_music": "auto", "clip_max_seconds": 45, "loop_friendly": True, "brand_handle": "@me"}
    monkeypatch.setattr(core._settings_store, "_data", data)
    monkeypatch.setattr(core._settings_store, "save", lambda: None)
    s = core.get_settings()
    assert s["default_music"] == "none" and s["clip_max_seconds"] == 58 and s["loop_friendly"] is False
    assert s["brand_handle"] == "@me"


def test_render_params_uses_ai_parts_and_subtitles():
    clip = {"segment": {"start": 10, "end": 50}, "parts": [[10, 25], [35, 50]], "mood": "dark",
            "subtitles": [{"start": 12, "end": 14, "text": "Привет"}], "accents": [20], "credit": "c"}
    p = pipeline.render_params(clip)
    assert p["segments"] == [[10.0, 25.0], [35.0, 50.0]]
    assert p["subtitles"] and p["accents"] == [20] and p["music"] == "none"
    p = pipeline.render_params({**clip, "segment": {"start": 12, "end": 40}})
    assert p["segments"] == [[12.0, 40.0]]            # отрезок изменён вручную — части не используются
    p = pipeline.render_params(clip, {"use_subtitles": False})
    assert p["subtitles"] == []


def test_foreign_subtitles_are_translated(sample_video, monkeypatch):
    from vh import gemini

    clip = library.add_clip({"anime": "Тест", "anime_name": "Тест", "title": "Сцена", "query": "q"})
    monkeypatch.setattr(discovery, "find_source_for_moment", lambda *a, **k: [{"url": "https://youtu.be/x", "title": "x", "views": 1}])
    monkeypatch.setattr(discovery, "video_info", lambda url: {"url": url, "duration": 20, "heatmap": [], "views": 1,
                                                             "subs_ru": {"kind": "auto", "url": "u", "orig_url": "o"}})
    monkeypatch.setattr(discovery, "download", lambda url, **k: {
        "path": str(sample_video), "duration": 20.0, "width": 1280, "height": 720, "offset": 0.0,
        "info": {"id": "x", "title": "x", "channel": "c", "views": 1, "url": url, "heatmap": []}})
    monkeypatch.setattr(discovery, "fetch_subtitles", lambda *a, **k: {
        "items": [{"start": 9, "end": 11, "text": "こんにちは"}], "lang": "ja"})
    monkeypatch.setattr(gemini, "available", lambda: True)
    monkeypatch.setattr(gemini, "analyze_video", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("skip")))
    monkeypatch.setattr(gemini, "translate_subtitles", lambda items, ctx, lang: [{**items[0], "text": "Привет"}])
    clip = pipeline.fetch_source(clip["id"])
    assert clip["subtitles"] == [{"start": 9, "end": 11, "text": "Привет"}]
    assert clip["subtitles_source"] == "gemini-translate"
