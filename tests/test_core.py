from vh import discovery, moments, publish
from vh.render import build_ass, clean_text, plan_segments


def test_seed_base_is_large_and_valid():
    cat = moments.catalog()
    assert len(cat) >= 30
    assert sum(a["total"] for a in cat) >= 60
    for a in cat:
        for m in a["moments"]:
            assert m["title"] and m["hook"] and m["mood"] in ("epic", "dark", "twist", "emotional", "romantic", "funny")


def test_find_anime_by_alias_and_character():
    assert moments.find_anime("Годжо против Сукуны #shorts")["key"] == "jujutsu kaisen"
    assert moments.find_anime("Levi vs Beast Titan")["key"] == "attack on titan"
    assert moments.find_anime("1000-7 канеки")["key"] == "tokyo ghoul"
    assert moments.find_anime("random cooking video") is None


def test_used_moment_leaves_fresh_list_and_triggers_refill(monkeypatch):
    started = []
    monkeypatch.setattr(moments, "ensure_fresh", lambda key, force=False: started.append(key))
    key = "tokyo ghoul"  # в базе 1 момент
    m = moments.list_moments(key, include_used=False)[0]
    moments.mark_used(m["id"], key, "c_test")
    fresh_ids = [x["id"] for x in moments.list_moments(key, include_used=False)]
    assert m["id"] not in fresh_ids
    assert started == [key]
    all_items = moments.list_moments(key)
    assert all_items[-1]["id"] == m["id"] and all_items[-1]["used"]
    moments.reset_usage(m["id"])
    assert m["id"] in [x["id"] for x in moments.list_moments(key, include_used=False)]


def test_add_moments_dedupes():
    key = "berserk"
    before = len(moments.list_moments(key))
    n = moments.add_moments(key, [{"title": "Затмение", "hook": "x"}, {"title": "Гатс против сотни", "hook": "Сто против одного", "mood": "epic"},
                                  {"title": "гатс против  СОТНИ!", "hook": "дубль"}], source="ai")
    assert n == 1
    assert len(moments.list_moments(key)) == before + 1


def test_refill_falls_back_to_templates_offline(monkeypatch):
    from vh import gemini
    monkeypatch.setattr(gemini, "available", lambda: False)
    monkeypatch.setattr(discovery, "popular_moments_for", lambda *a, **k: [])
    moments.register_anime  # noqa: B018
    anime = {"key": "test show", "en": "Test Show", "ru": "Тестовое шоу", "genres": ["Comedy"], "moments": []}
    res = moments.refill(anime)
    assert res["added"] >= 1
    assert moments.fresh_count("test show") >= 1


def test_heatmap_windows_pick_peak():
    heat = [{"start_time": i, "end_time": i + 1, "value": 1.0 if 50 <= i < 70 else 0.1} for i in range(120)]
    w = discovery.heatmap_windows(heat, window=20, top_k=2, duration=120)
    assert w[0]["source"] == "heatmap"
    assert 45 <= w[0]["start"] <= 55


def test_merge_suggestions_rewards_agreement():
    a = [{"start": 10, "end": 40, "score": 0.7, "source": "heatmap"}]
    b = [{"start": 12, "end": 42, "score": 0.6, "source": "local"}, {"start": 100, "end": 130, "score": 1.0, "source": "local"}]
    merged = discovery.merge_suggestions(a, b)
    assert merged[0]["start"] in (10, 12)
    assert merged[0]["confirmed_by"]


def test_plan_segments_caps_duration():
    assert plan_segments([(0, 100)], 200, 45) == [(0.0, 45.0)]
    assert plan_segments([], 20, 45) == [(0.0, 20.0)]


def test_ass_contains_all_layers_and_no_emoji():
    ass = build_ass({"hook": "Бой 🔥", "caption": "Цитата", "handle": "@x", "credit": "© студия", "anime_label": "Аниме"}, 20, (0, 656, 1080, 608))
    assert "Бой" in ass and "🔥" not in ass
    for part in ("Style: Hook", "Цитата", "@x", "© студия", "\\clip"):
        assert part in ass
    assert clean_text("a{b}\\c") == "a(b)/c"


def test_offline_captions_respect_limits():
    clip = {"anime": "Магическая битва", "anime_name": "Магическая битва (Jujutsu Kaisen)", "anime_en": "Jujutsu Kaisen",
            "title": "Сукуна против Махораги", "hook": "Сукуна показал истинное безумие", "mood": "dark",
            "credit": "Аниме: Jujutsu Kaisen · © MAPPA · фан-обзор"}
    caps = publish.captions_offline(clip)
    assert len(caps["youtube"]["title"]) <= 100 and "#shorts" in caps["youtube"]["title"]
    assert "© MAPPA" in caps["instagram"]["caption"]
    assert "JujutsuKaisen" in caps["tiktok"]["caption"]


def test_tiktok_chunking_rules():
    assert publish.tiktok_chunks(3_000_000) == [(0, 2_999_999)]
    size = 150 * 1024 * 1024
    ch = publish.tiktok_chunks(size)
    assert ch[0][0] == 0 and ch[-1][1] == size - 1
    assert all(b - a + 1 >= 5 * 1024 * 1024 for a, b in ch)


def test_channel_signature_and_links_added_once():
    s = {"caption_signature": "Подписывайся на канал", "telegram": "https://t.me/hook", "link_donate": "https://boosty.to/hook",
         "channel_hashtags": "anime_hook, аниме_моменты", "links_in_short_captions": False}
    caps = {"youtube": {"title": "t", "description": "Описание", "tags": ["anime"]},
            "tiktok": {"caption": "Тикток #anime_hook"}, "instagram": {"caption": "Инста"}}
    publish.apply_channel(caps, s)
    yt = caps["youtube"]["description"]
    assert "Подписывайся на канал" in yt and "Telegram: https://t.me/hook" in yt and "Поддержать канал: https://boosty.to/hook" in yt
    assert "#anime_hook" in yt and "#аниме_моменты" in yt
    assert "t.me" not in caps["tiktok"]["caption"] and "Подписывайся на канал" in caps["tiktok"]["caption"]
    assert caps["tiktok"]["caption"].count("#anime_hook") == 1
    assert "anime_hook" in caps["youtube"]["tags"]
    publish.apply_channel(caps, s)   # повторный вызов не дублирует
    assert caps["youtube"]["description"].count("Telegram:") == 1
