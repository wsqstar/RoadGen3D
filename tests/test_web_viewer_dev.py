from __future__ import annotations

import sys
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
    viewer_dir = repo_root / "web" / "viewer"
    dist_dir = viewer_dir / "dist"
    dist_dir.mkdir(parents=True, exist_ok=True)
    (dist_dir / "index.html").write_text("<!doctype html>", encoding="utf-8")
    layout_path = repo_root / "artifacts" / "real" / "scene_layout.json"
    layout_path.parent.mkdir(parents=True, exist_ok=True)
    layout_path.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(viewer, "ROOT", repo_root)
    monkeypatch.setattr(viewer, "VIEWER_DIR", viewer_dir)
    monkeypatch.setattr(viewer, "VIEWER_DIST_DIR", dist_dir)

    url = viewer.build_web_viewer_url(layout_path)

    assert url.startswith("/web-viewer/?layout=")
    assert str(layout_path) not in url
    assert "scene_layout.json" in url


def test_build_web_viewer_url_rejects_layouts_outside_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo_root = (tmp_path / "repo").resolve()
    repo_root.mkdir(parents=True, exist_ok=True)
    external_layout = (tmp_path / "outside" / "scene_layout.json").resolve()
    external_layout.parent.mkdir(parents=True, exist_ok=True)
    external_layout.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(viewer, "ROOT", repo_root)
    with pytest.raises(viewer.WebViewerError, match="inside repo root"):
        viewer.build_web_viewer_url(external_layout)


def test_ensure_web_viewer_assets_reports_missing_build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo_root = (tmp_path / "repo").resolve()
    viewer_dir = repo_root / "web" / "viewer"
    viewer_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(viewer, "ROOT", repo_root)
    monkeypatch.setattr(viewer, "VIEWER_DIR", viewer_dir)
    monkeypatch.setattr(viewer, "VIEWER_DIST_DIR", viewer_dir / "dist")

    with pytest.raises(viewer.WebViewerError, match="npm --prefix web/viewer install && npm --prefix web/viewer run build"):
        viewer.ensure_web_viewer_assets()
