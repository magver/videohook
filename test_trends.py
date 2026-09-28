import os
import time
from pathlib import Path
import tkinter.messagebox

from trends import (
    GENRE_MOMENTS,
    ICONIC_MOMENTS_DB,
    MALIntegration,
    TrendsCache,
    fetch_top_anime,
    fetch_trending_with_episodes,
    generate_moments_for_anime,
    get_iconic_moments_for_anime,
    search_anime_by_title,
    search_youtube_moments,
)
from gui import VideoHookApp

# Suppress messageboxes in automated tests
tkinter.messagebox.showinfo = lambda *args, **kwargs: None
tkinter.messagebox.showwarning = lambda *args, **kwargs: None
tkinter.messagebox.showerror = lambda *args, **kwargs: None


def test_mal_integration():
    print("Testing MALIntegration (Jikan API v4)...")
    mal = MALIntegration()
    assert mal.BASE_URL == "https://api.jikan.moe/v4"
    assert hasattr(mal, "get_top_anime")
    assert hasattr(mal, "search_anime")
    assert hasattr(mal, "get_anime_episodes")
    print("MALIntegration structure and methods verified!")


def test_genre_moments_and_generation():
    print("Testing GENRE_MOMENTS and auto-generation...")
    expected_genres = ["Action", "Romance", "Horror", "Comedy", "Drama", "Fantasy", "Sci-Fi"]
    for g in expected_genres:
        assert g in GENRE_MOMENTS, f"Missing genre {g} in GENRE_MOMENTS"
        assert len(GENRE_MOMENTS[g]) >= 3, f"Genre {g} has fewer than 3 moments"

    # Curated anime
    ds_moments = generate_moments_for_anime("Demon Slayer: Kimetsu no Yaiba", ["Action", "Fantasy"], 85.0)
    assert len(ds_moments) >= 2, "Curated moments not returned for Demon Slayer"
    assert ds_moments[0].get("is_iconic") is True, "Curated moments should have is_iconic=True"

    # Genre-generated anime
    gen_moments = generate_moments_for_anime("Frieren: Beyond Journey's End", ["Fantasy", "Drama"], 90.0)
    assert len(gen_moments) >= 2, "Failed to auto-generate moments for Frieren based on Fantasy/Drama"
    assert any("Магии" in m["hook"] or "Фэнтези" in m["title"] or "Fantasy" in m["title"] or "Drama" in m["title"] for m in gen_moments)
    print("GENRE_MOMENTS and moment generation verified!")


def test_trends_cache():
    print("Testing TrendsCache 24h TTL...")
    test_cache_file = "test_trends_cache.json"
    cache = TrendsCache(cache_file=test_cache_file, ttl=5)

    sample_items = [
        {"id": 101, "title": "Test Anime 1", "score": 85, "genres": ["Action"]},
        {"id": 102, "title": "Test Anime 2", "score": 75, "genres": ["Romance"]},
    ]
    cache.save(sample_items, key="test_key")

    loaded = cache.load(key="test_key")
    assert len(loaded) == 2, f"Expected 2 cached items, got {len(loaded)}"
    assert loaded[0]["title"] == "Test Anime 1"

    # Clear test
    cache.clear()
    assert not os.path.exists(test_cache_file), "Cache file was not removed after clear"
    print("TrendsCache save, load, and clear verified!")


def test_fetch_trending_with_episodes():
    print("Testing fetch_trending_with_episodes...")
    items = fetch_trending_with_episodes(limit=10, use_cache=True)
    assert len(items) > 0, "Trending items list is empty"
    first = items[0]
    assert "title" in first, "Item missing 'title'"
    assert "score" in first, "Item missing 'score'"
    assert "genres" in first, "Item missing 'genres'"
    assert "moments" in first, "Item missing 'moments'"
    assert len(first["moments"]) > 0, "No moments attached to trending anime"
    print(f"fetch_trending_with_episodes returned {len(items)} items, top: {first['title']} (Score: {first['score']})")


def test_gui_trends_filters():
    print("Testing GUI filters in VideoHookApp...")
    app = VideoHookApp()
    app.update()

    # Check widgets created in Stage 5F
    assert hasattr(app, "genre_dropdown"), "genre_dropdown not found in app"
    assert hasattr(app, "min_score_slider"), "min_score_slider not found in app"
    assert hasattr(app, "yt_only_cb"), "yt_only_cb not found in app"
    assert hasattr(app, "iconic_only_cb"), "iconic_only_cb not found in app"
    assert hasattr(app, "refresh_db_btn"), "refresh_db_btn not found in app"

    # Inject mock data into raw_trends_data
    app.raw_trends_data = [
        {"id": 1, "title": "Epic Action", "score": 85, "episodes": 12, "genres": ["Action"], "has_youtube": True, "has_iconic": True},
        {"id": 2, "title": "Sweet Romance", "score": 65, "episodes": 12, "genres": ["Romance"], "has_youtube": True, "has_iconic": False},
        {"id": 3, "title": "Low Score Horror", "score": 50, "episodes": 12, "genres": ["Horror"], "has_youtube": False, "has_iconic": False},
        {"id": 4, "title": "Cult Classic Drama", "score": 92, "episodes": 24, "genres": ["Drama"], "has_youtube": True, "has_iconic": True},
    ]

    # 1. Filter by Genre Action
    app.genre_filter_var.set("Action")
    app._apply_trends_filters()
    assert len(app.trends_data) == 1 and app.trends_data[0]["title"] == "Epic Action", "Genre filter failed"

    # 2. Reset genre to "Все", filter by Min Rating 8.0 (80%)
    app.genre_filter_var.set("Все")
    app.min_score_slider.set(8.0)
    app._apply_trends_filters()
    assert len(app.trends_data) == 2, f"Expected 2 items with score >= 80, got {len(app.trends_data)}"

    # 3. Filter only iconic moments
    app.iconic_only_var.set(True)
    app._apply_trends_filters()
    assert all(item["has_iconic"] for item in app.trends_data), "Iconic only filter failed"

    # 4. Filter only with YouTube
    app.yt_only_var.set(True)
    app._apply_trends_filters()
    assert all(item["has_youtube"] for item in app.trends_data), "YouTube only filter failed"

    print("GUI filters (genre, min score, iconic, youtube) verified!")
    app.destroy()


if __name__ == "__main__":
    test_mal_integration()
    test_genre_moments_and_generation()
    test_trends_cache()
    test_fetch_trending_with_episodes()
    test_gui_trends_filters()
    print("\nALL Stage 5 Trends tests passed 100%!")
