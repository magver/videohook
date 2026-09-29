from vh import gemini, library, style


def test_rebuild_keeps_user_rules(monkeypatch):
    style.write_guide(style.join_guide("- Хук всегда вопросом", "- старое правило"))
    style.add_feedback(library.add_clip({"anime": "A", "title": "T"}), 1, "финал огонь")
    seen = {}

    def fake_build(user, ai, examples, feedback, progress=None):
        seen.update(user=user, ai=ai, fb=feedback)
        return "### Монтаж и темп\n- новое правило"

    monkeypatch.setattr(gemini, "build_style_guide", fake_build)
    text = style.rebuild_guide()
    assert "- Хук всегда вопросом" in text and "- новое правило" in text and "старое правило" not in text
    assert seen["user"] == "- Хук всегда вопросом" and seen["fb"][-1]["comment"] == "финал огонь"


def test_style_injected_into_prompts(monkeypatch):
    style.write_guide(style.join_guide("- Никаких спойлеров финала", "- Длина 45–55 c"))
    prompts = []
    monkeypatch.setattr(gemini, "generate", lambda prompt, **k: prompts.append(prompt) or {"moments": [], "scores": [],
                                                                                              "youtube": {}})
    gemini.suggest_moments("Test", [])
    gemini.rate_moments("Test", [], [{"id": "m1", "title": "x"}], {})
    gemini.write_captions({"title": "t"}, {})
    assert all("СТИЛЬ КАНАЛА" in p and "Никаких спойлеров финала" in p for p in prompts)
    assert "<!--" not in style.prompt_block()


def test_feedback_batch_triggers_rebuild(monkeypatch):
    started = []
    monkeypatch.setattr(style, "rebuild_async", lambda: started.append(1))
    with style._store.lock:
        style._store.load()["since_rebuild"] = 0
    for i in range(style.FEEDBACK_BATCH):
        style.add_feedback(library.add_clip({"anime": "A", "title": f"T{i}"}), -1 if i % 2 else 1, "")
    assert started == [1]
    clip = library.list_clips()[0]
    assert "feedback" in clip


def test_study_example_flow(monkeypatch, sample_video):
    from vh import discovery

    monkeypatch.setattr(discovery, "video_info", lambda url: {"title": "Пример", "duration": 20})
    monkeypatch.setattr(discovery, "download", lambda url, **k: {
        "path": str(sample_video), "duration": 20.0, "width": 1280, "height": 720, "offset": 0.0,
        "info": {"id": "x", "title": "Пример", "channel": "c", "views": 5, "url": url}})
    got = {}

    def fake_study(path, sheets, audio, note, metrics, title="", progress=None):
        got.update(sheets=sheets, metrics=metrics, note=note)
        return {"summary": "Цепляет темпом", "lessons": ["Склейка каждые 1.5 c на действии"], "avoid": []}

    monkeypatch.setattr(gemini, "study_example", fake_study)
    monkeypatch.setattr(gemini, "build_style_guide", lambda *a, **k: "### Монтаж и темп\n- Склейка каждые 1.5 c")
    ex = style.study_example("https://youtu.be/x", "нравится темп")
    assert ex["lessons"] == ["Склейка каждые 1.5 c на действии"]
    assert got["metrics"]["duration"] > 19 and got["sheets"] and got["note"] == "нравится темп"
    assert "Склейка каждые 1.5 c" in style.read_guide()
    assert style.list_examples()[0]["id"] == ex["id"]
