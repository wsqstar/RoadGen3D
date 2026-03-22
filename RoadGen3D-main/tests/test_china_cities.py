"""Tests for china_cities registry integrity and lookup functions."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest

from roadgen3d.china_cities import (
    CHINA_CITY_REGISTRY,
    CityRecord,
    get_all_bboxes,
    get_city_by_name,
    get_city_choices,
)


class TestRegistryIntegrity:
    """Verify the city registry data is well-formed."""

    def test_registry_not_empty(self):
        assert len(CHINA_CITY_REGISTRY) >= 70

    def test_city_records_are_frozen(self):
        city = CHINA_CITY_REGISTRY[0]
        assert isinstance(city, CityRecord)
        with pytest.raises(AttributeError):
            city.name_zh = "changed"  # type: ignore[misc]

    def test_city_records_unique_name_en(self):
        names = [c.name_en for c in CHINA_CITY_REGISTRY]
        assert len(names) == len(set(names)), f"Duplicate name_en: {[n for n in names if names.count(n) > 1]}"

    def test_bbox_valid_range(self):
        """All bboxes should be within China's geographic bounds."""
        for city in CHINA_CITY_REGISTRY:
            min_lon, min_lat, max_lon, max_lat = city.bbox
            assert 73.0 <= min_lon <= 135.0, f"{city.name_en} min_lon={min_lon}"
            assert 18.0 <= min_lat <= 54.0, f"{city.name_en} min_lat={min_lat}"
            assert 73.0 <= max_lon <= 135.0, f"{city.name_en} max_lon={max_lon}"
            assert 18.0 <= max_lat <= 54.0, f"{city.name_en} max_lat={max_lat}"
            assert min_lon < max_lon, f"{city.name_en} min_lon >= max_lon"
            assert min_lat < max_lat, f"{city.name_en} min_lat >= max_lat"

    def test_bbox_reasonable_size(self):
        """Each bbox should span roughly 0.001-0.01 degrees (~100m to ~1km)."""
        for city in CHINA_CITY_REGISTRY:
            min_lon, min_lat, max_lon, max_lat = city.bbox
            lon_span = max_lon - min_lon
            lat_span = max_lat - min_lat
            assert 0.001 <= lon_span <= 0.02, f"{city.name_en} lon_span={lon_span}"
            assert 0.001 <= lat_span <= 0.02, f"{city.name_en} lat_span={lat_span}"

    def test_all_fields_nonempty(self):
        for city in CHINA_CITY_REGISTRY:
            assert city.name_zh, f"{city.name_en} has empty name_zh"
            assert city.name_en, f"city has empty name_en"
            assert city.province, f"{city.name_en} has empty province"
            assert len(city.bbox) == 4, f"{city.name_en} bbox length != 4"


class TestLookup:
    """Test city lookup functions."""

    def test_get_city_by_name_zh(self):
        city = get_city_by_name("北京")
        assert city is not None
        assert city.name_en == "beijing"

    def test_get_city_by_name_en(self):
        city = get_city_by_name("shanghai")
        assert city is not None
        assert city.name_zh == "上海"

    def test_get_city_by_name_en_case_insensitive(self):
        city = get_city_by_name("BEIJING")
        assert city is not None
        assert city.name_en == "beijing"

    def test_get_city_by_name_not_found(self):
        assert get_city_by_name("atlantis") is None
        assert get_city_by_name("不存在的城市") is None

    def test_get_city_by_name_whitespace(self):
        city = get_city_by_name("  shenzhen  ")
        assert city is not None
        assert city.name_zh == "深圳"


class TestHelpers:
    """Test convenience helper functions."""

    def test_get_all_bboxes_length(self):
        bboxes = get_all_bboxes()
        assert len(bboxes) == len(CHINA_CITY_REGISTRY)

    def test_get_all_bboxes_are_tuples(self):
        for bbox in get_all_bboxes():
            assert len(bbox) == 4
            assert all(isinstance(v, float) for v in bbox)

    def test_get_city_choices_format(self):
        choices = get_city_choices()
        assert len(choices) == len(CHINA_CITY_REGISTRY)
        for display, value in choices:
            assert isinstance(display, str)
            assert isinstance(value, str)
            assert value  # name_en should not be empty
            # Display should contain both Chinese and English
            city = get_city_by_name(value)
            assert city is not None
            assert city.name_zh in display

    def test_get_city_choices_first_entry(self):
        choices = get_city_choices()
        display, value = choices[0]
        assert value == "beijing"
        assert "北京" in display
