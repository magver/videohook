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
    assert 5 <= seg["start"] <= 13, f"громкий участок 8–12 c, получено {seg}"

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
