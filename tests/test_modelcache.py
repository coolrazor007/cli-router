from cli_router.modelcache import ModelCache


def test_cache_roundtrips_models(tmp_path):
    path = tmp_path / "model-cache.yaml"
    cache = ModelCache.load(path)
    cache.set("grok", ["grok-4.5", "grok-composer-2.5-fast"])
    cache.save()

    reloaded = ModelCache.load(path)
    assert reloaded.get("grok") == ["grok-4.5", "grok-composer-2.5-fast"]


def test_cache_load_missing_file_is_empty(tmp_path):
    cache = ModelCache.load(tmp_path / "absent.yaml")
    assert cache.get("codex") == []


def test_cache_set_dedupes_and_drops_empty(tmp_path):
    cache = ModelCache.load(tmp_path / "c.yaml")
    cache.set("codex", ["gpt-5.6-sol", "gpt-5.6-sol", "", "gpt-5.5"])
    assert cache.get("codex") == ["gpt-5.6-sol", "gpt-5.5"]


def test_cache_set_empty_list_removes_provider(tmp_path):
    cache = ModelCache.load(tmp_path / "c.yaml")
    cache.set("codex", ["gpt-5.6-sol"])
    cache.set("codex", [])
    assert cache.get("codex") == []


def test_cache_load_ignores_corrupt_file(tmp_path):
    path = tmp_path / "corrupt.yaml"
    path.write_text("this: [is not: valid", encoding="utf-8")
    assert ModelCache.load(path).models == {}
