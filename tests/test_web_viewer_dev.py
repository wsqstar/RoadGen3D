from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import roadgen3d.web_viewer_dev as viewer


def test_build_web_viewer_url_accepts_repo_layout_and_encodes_query(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo_root = (tmp_path / "repo").resolve()
    layout_path = repo_root / "artifacts" / "real" / "scene_layout.json"
    layout_path.parent.mkdir(parents=True, exist_ok=True)
    layout_path.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(viewer, "ROOT", repo_root)

    url = viewer.build_web_viewer_url(layout_path)

    assert url.startswith("http://127.0.0.1:4173/?layout=")
    assert str(layout_path) not in url
    assert "scene_layout.json" in url


def test_build_web_viewer_dev_command_allows_external_layout_parent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo_root = (tmp_path / "repo").resolve()
    viewer_dir = repo_root / "web" / "viewer"
    viewer_dir.mkdir(parents=True, exist_ok=True)
    external_layout = (tmp_path / "outside" / "scene_layout.json").resolve()
    external_layout.parent.mkdir(parents=True, exist_ok=True)
    external_layout.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(viewer, "ROOT", repo_root)
    monkeypatch.setattr(viewer, "VIEWER_DIR", viewer_dir)

    command = viewer.build_web_viewer_dev_command(external_layout)

    assert "ROADGEN_VIEWER_ALLOWED_ROOTS=" in command
    assert str(external_layout.parent) in command
    assert "npm --prefix" in command
    assert "--open" in command
    assert "scene_layout.json" in command


def test_is_repo_local_path_is_false_for_layouts_outside_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo_root = (tmp_path / "repo").resolve()
    repo_root.mkdir(parents=True, exist_ok=True)
    external_layout = (tmp_path / "outside" / "scene_layout.json").resolve()
    external_layout.parent.mkdir(parents=True, exist_ok=True)
    external_layout.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(viewer, "ROOT", repo_root)
    assert viewer.is_repo_local_path(external_layout) is False


def test_ensure_web_viewer_assets_reports_missing_build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo_root = (tmp_path / "repo").resolve()
    viewer_dir = repo_root / "web" / "viewer"
    viewer_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(viewer, "ROOT", repo_root)
    monkeypatch.setattr(viewer, "VIEWER_DIR", viewer_dir)
    monkeypatch.setattr(viewer, "VIEWER_DIST_DIR", viewer_dir / "dist")

    with pytest.raises(viewer.WebViewerError, match="npm --prefix web/viewer install && npm --prefix web/viewer run build"):
        viewer.ensure_web_viewer_assets()


def test_discover_recent_scene_layouts_sorts_newest_first_and_limits_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    repo_root = (tmp_path / "repo").resolve()
    newest_dir = repo_root / "artifacts" / "newest_run"
    older_dir = repo_root / "artifacts" / "older_run"
    ignored_dir = repo_root / "web" / "viewer" / "node_modules" / "fake_pkg"
    newest_dir.mkdir(parents=True, exist_ok=True)
    older_dir.mkdir(parents=True, exist_ok=True)
    ignored_dir.mkdir(parents=True, exist_ok=True)

    older_layout = older_dir / "scene_layout.json"
    newest_layout = newest_dir / "scene_layout.json"
    ignored_layout = ignored_dir / "scene_layout.json"
    older_layout.write_text("{}", encoding="utf-8")
    newest_layout.write_text("{}", encoding="utf-8")
    ignored_layout.write_text("{}", encoding="utf-8")

    base_time = time.time()
    os.utime(older_layout, (base_time - 60, base_time - 60))
    os.utime(newest_layout, (base_time, base_time))
    os.utime(ignored_layout, (base_time + 60, base_time + 60))

    monkeypatch.setattr(viewer, "ROOT", repo_root)

    results = viewer.discover_recent_scene_layouts(limit=1)

    assert len(results) == 1
    assert results[0]["layout_path"] == str(newest_layout.resolve())
    assert "node_modules" not in results[0]["relative_path"]


def test_build_recent_layouts_payload_includes_display_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    repo_root = (tmp_path / "repo").resolve()
    scene_dir = repo_root / "artifacts" / "demo_scene"
    scene_dir.mkdir(parents=True, exist_ok=True)
    layout_path = scene_dir / "scene_layout.json"
    layout_path.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(viewer, "ROOT", repo_root)

    payload = viewer.build_recent_layouts_payload(limit=5)

    assert len(payload["results"]) == 1
    entry = payload["results"][0]
    assert entry["layout_path"] == str(layout_path.resolve())
    assert entry["label"].startswith("demo_scene · ")
    assert entry["relative_path"].endswith("artifacts/demo_scene/scene_layout.json")
    assert "updated_at" in entry


def test_cache_scene_layout_for_viewer_mirrors_external_layout_into_repo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    repo_root = (tmp_path / "repo").resolve()
    external_layout = (tmp_path / "outside" / "run_001" / "scene_layout.json").resolve()
    external_layout.parent.mkdir(parents=True, exist_ok=True)
    external_layout.write_text(json.dumps({"summary": {"ok": True}, "outputs": {}}), encoding="utf-8")

    monkeypatch.setattr(viewer, "ROOT", repo_root)
    monkeypatch.setattr(viewer, "VIEWER_LAYOUTS_DIR", (repo_root / "artifacts" / "web_viewer_layouts").resolve())

    cached = viewer.cache_scene_layout_for_viewer(external_layout)

    assert cached.exists()
    assert str(cached).startswith(str(repo_root))
    assert json.loads(cached.read_text(encoding="utf-8"))["summary"]["ok"] is True


def test_cache_scene_layout_for_viewer_sanitizes_infinity_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    repo_root = (tmp_path / "repo").resolve()
    external_layout = (tmp_path / "outside" / "run_002" / "scene_layout.json").resolve()
    external_layout.parent.mkdir(parents=True, exist_ok=True)
    external_layout.write_text('{"summary":{"dist_to_nearest_entrance_m": Infinity},"outputs":{}}', encoding="utf-8")

    monkeypatch.setattr(viewer, "ROOT", repo_root)
    monkeypatch.setattr(viewer, "VIEWER_LAYOUTS_DIR", (repo_root / "artifacts" / "web_viewer_layouts").resolve())

    cached = viewer.cache_scene_layout_for_viewer(external_layout)
    cached_text = cached.read_text(encoding="utf-8")

    assert "Infinity" not in cached_text
    assert json.loads(cached_text)["summary"]["dist_to_nearest_entrance_m"] is None


def test_cache_scene_layout_for_viewer_sanitizes_repo_local_layouts_too(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    repo_root = (tmp_path / "repo").resolve()
    layout_path = (repo_root / "artifacts" / "run_003" / "scene_layout.json").resolve()
    layout_path.parent.mkdir(parents=True, exist_ok=True)
    layout_path.write_text('{"summary":{"clearance_m": Infinity},"outputs":{}}', encoding="utf-8")

    monkeypatch.setattr(viewer, "ROOT", repo_root)
    monkeypatch.setattr(viewer, "VIEWER_LAYOUTS_DIR", (repo_root / "artifacts" / "web_viewer_layouts").resolve())

    cached = viewer.cache_scene_layout_for_viewer(layout_path)
    cached_text = cached.read_text(encoding="utf-8")

    assert cached != layout_path
    assert str(cached).startswith(str(viewer.VIEWER_LAYOUTS_DIR))
    assert "Infinity" not in cached_text
    assert json.loads(cached_text)["summary"]["clearance_m"] is None
