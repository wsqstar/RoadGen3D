from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.objaverse_import import (
    ObjaverseCandidate,
    ObjaverseImportResult,
    ObjaverseTargetSpec,
    append_manifest_rows,
    collect_lvis_candidate_uids,
    compose_manifest_row,
    default_target_specs,
    import_objaverse_assets,
    recommended_default_categories,
    score_candidate,
)
from scripts import m2_14_import_objaverse as objaverse_script


def _annotation(
    *,
    uid: str = "uid_1",
    name: str = "Outdoor bench",
    description: str = "A low poly street bench",
    license_name: str = "cc-by",
    face_count: int = 420,
    vertex_count: int = 250,
    downloadable: bool = True,
) -> dict[str, object]:
    return {
        "uid": uid,
        "name": name,
        "description": description,
        "license": license_name,
        "faceCount": face_count,
        "vertexCount": vertex_count,
        "isDownloadable": downloadable,
        "viewerUrl": f"https://example.com/{uid}",
        "uri": f"https://objaverse.example/{uid}",
        "tags": [{"name": "street"}, {"name": "outdoor"}, {"name": "lowpoly"}],
        "categories": [{"name": "bench"}],
        "thumbnails": {"images": [{"url": f"https://img.example/{uid}.png", "width": 512, "height": 512}]},
    }


def test_recommended_default_categories_focus_on_first_wave_street_assets():
    assert recommended_default_categories() == ("bench", "lamp", "trash", "mailbox", "tree")


def test_default_target_specs_support_tree_imports_without_making_them_first_wave_default():
    specs = {spec.roadgen_category: spec for spec in default_target_specs(["tree", "bench"])}

    assert "tree" in specs
    assert specs["tree"].lvis_categories == ("tree",)
    assert "stylized" in specs["tree"].positive_keywords
    assert "potted" in specs["tree"].negative_keywords


def test_collect_lvis_candidate_uids_dedupes_alias_overlap():
    specs = (
        ObjaverseTargetSpec(
            roadgen_category="lamp",
            lvis_categories=("streetlight", "lamppost"),
            positive_keywords=("lamp",),
        ),
    )
    annotations = {
        "streetlight": ["uid_a", "uid_b"],
        "lamppost": ["uid_b", "uid_c"],
    }

    result = collect_lvis_candidate_uids(annotations, specs)

    assert result["lamp"] == [("uid_a", "streetlight"), ("uid_b", "streetlight"), ("uid_c", "lamppost")]


def test_score_candidate_filters_editorial_and_out_of_range_faces():
    spec = ObjaverseTargetSpec(
        roadgen_category="bench",
        lvis_categories=("bench",),
        positive_keywords=("bench", "outdoor"),
        negative_keywords=("indoor",),
        min_face_count=100,
        max_face_count=1000,
    )

    accepted = score_candidate(_annotation(), spec, lvis_category="bench")
    rejected_editorial = score_candidate(
        _annotation(uid="uid_2", license_name="Editorial"),
        spec,
        lvis_category="bench",
    )
    rejected_low_poly = score_candidate(
        _annotation(uid="uid_3", face_count=12),
        spec,
        lvis_category="bench",
    )

    assert accepted is not None
    assert accepted.score > 1.0
    assert rejected_editorial is None
    assert rejected_low_poly is None


def test_compose_manifest_row_includes_required_fields_and_quality_metrics(tmp_path: Path):
    candidate = ObjaverseCandidate(
        uid="uid_manifest",
        roadgen_category="bench",
        lvis_category="bench",
        score=2.3,
        annotation=_annotation(uid="uid_manifest"),
        reasons=("lvis:bench", "kw+:bench"),
    )

    row = compose_manifest_row(
        candidate,
        mesh_path=str(tmp_path / "bench.glb"),
        latents_dir=tmp_path / "latents",
        split="train",
    )

    assert row["asset_id"] == "objaverse_bench_uid_manifest"
    assert row["category"] == "bench"
    assert row["asset_role"] == "street_furniture"
    assert row["mesh_face_count"] == 420
    assert row["quality_metrics"]["face_count"] == 420
    assert str(row["latent_path"]).endswith("objaverse_bench_uid_manifest.pt")


def test_import_objaverse_assets_selects_and_reports(monkeypatch, tmp_path: Path):
    annotations = {
        "bench_uid": _annotation(uid="bench_uid", name="Street bench"),
        "lamp_uid": _annotation(uid="lamp_uid", name="Lamppost", description="Urban streetlight"),
    }

    monkeypatch.setattr(
        "roadgen3d.objaverse_import.load_lvis_annotations",
        lambda cache_root: {"bench": ["bench_uid"], "streetlight": ["lamp_uid"]},
    )
    monkeypatch.setattr(
        "roadgen3d.objaverse_import.load_annotation_subset",
        lambda cache_root, uids: {uid: annotations[uid] for uid in uids},
    )
    monkeypatch.setattr(
        "roadgen3d.objaverse_import.download_selected_objects",
        lambda cache_root, candidates, download_processes=1: {
            candidate.uid: str((tmp_path / f"{candidate.uid}.glb").resolve()) for candidate in candidates
        },
    )

    result = import_objaverse_assets(
        cache_root=tmp_path / "cache",
        latents_dir=tmp_path / "latents",
        requested_categories=["bench", "lamp"],
        max_per_category=1,
        download_processes=1,
    )

    assert len(result.manifest_rows) == 2
    assert result.report["selected_by_category"] == {"bench": 1, "lamp": 1}
    assert set(result.report["manifest_asset_ids"]) == {
        "objaverse_bench_bench_uid",
        "objaverse_lamp_lamp_uid",
    }


def test_import_objaverse_assets_supports_tree_category(monkeypatch, tmp_path: Path):
    tree_annotation = _annotation(
        uid="tree_uid",
        name="Stylized street tree",
        description="Outdoor low poly maple tree",
        face_count=1200,
    )
    tree_annotation["categories"] = [{"name": "tree"}]
    tree_annotation["tags"] = [{"name": "tree"}, {"name": "outdoor"}, {"name": "stylized"}]

    monkeypatch.setattr(
        "roadgen3d.objaverse_import.load_lvis_annotations",
        lambda cache_root: {"tree": []},
    )
    monkeypatch.setattr(
        "roadgen3d.objaverse_import.load_annotation_subset",
        lambda cache_root, uids: {},
    )
    monkeypatch.setattr(
        "roadgen3d.objaverse_import.load_all_annotations",
        lambda cache_root: {"tree_uid": tree_annotation},
    )
    monkeypatch.setattr(
        "roadgen3d.objaverse_import.download_selected_objects",
        lambda cache_root, candidates, download_processes=1: {
            candidate.uid: str((tmp_path / f"{candidate.uid}.glb").resolve()) for candidate in candidates
        },
    )

    result = import_objaverse_assets(
        cache_root=tmp_path / "cache",
        latents_dir=tmp_path / "latents",
        requested_categories=["tree"],
        max_per_category=1,
        download_processes=1,
    )

    assert len(result.manifest_rows) == 1
    row = result.manifest_rows[0]
    assert row["category"] == "tree"
    assert row["source"] == "objaverse_import"
    assert row["theme_tags"] == ["green", "residential", "civic"]
    assert result.report["metadata_scan_used"] is True


def test_run_objaverse_import_writes_clean_manifest_and_append_dedupes(monkeypatch, tmp_path: Path):
    mesh_path = (tmp_path / "cache" / "asset.glb").resolve()
    mesh_path.parent.mkdir(parents=True, exist_ok=True)
    mesh_path.write_bytes(b"glb")

    candidate = ObjaverseCandidate(
        uid="uid_script",
        roadgen_category="bench",
        lvis_category="bench",
        score=2.0,
        annotation=_annotation(uid="uid_script"),
        reasons=("lvis:bench",),
    )
    row = compose_manifest_row(
        candidate,
        mesh_path=str(mesh_path),
        latents_dir=tmp_path / "latents",
        split="train",
    )
    fake_result = ObjaverseImportResult(
        manifest_rows=(row,),
        selected_candidates=(candidate,),
        downloaded_paths={"uid_script": str(mesh_path)},
        cache_root=(tmp_path / "cache").resolve(),
        report={"selected_by_category": {"bench": 1}},
    )
    monkeypatch.setattr(objaverse_script, "import_objaverse_assets", lambda **kwargs: fake_result)

    output_manifest = tmp_path / "objaverse_assets_manifest.jsonl"
    append_manifest = tmp_path / "real_assets_manifest.jsonl"
    append_manifest.write_text(
        json.dumps(
            {
                "asset_id": "existing_asset",
                "category": "bench",
                "text_desc": "existing",
                "mesh_path": str(mesh_path),
                "latent_path": str(tmp_path / "latents" / "existing_asset.pt"),
                "license": "cc-by",
                "source": "seed",
                "split": "train",
                "mesh_face_count": 320,
            },
            ensure_ascii=True,
        )
        + "\n",
        encoding="utf-8",
    )

    report = objaverse_script.run_objaverse_import(
        cache_root=tmp_path / "cache",
        output_manifest=output_manifest,
        latents_dir=tmp_path / "latents",
        requested_categories=["bench"],
        max_per_category=1,
        download_processes=1,
        split="train",
        clean_manifest=True,
        report_out=tmp_path / "report.json",
        append_manifest=append_manifest,
        rebuild_index=False,
        artifacts_dir=tmp_path / "artifacts",
        model_name="openai/clip-vit-base-patch32",
        model_dir=None,
        local_files_only=True,
        device="cpu",
    )

    output_rows = [json.loads(line) for line in output_manifest.read_text(encoding="utf-8").splitlines() if line.strip()]
    appended_rows = [json.loads(line) for line in append_manifest.read_text(encoding="utf-8").splitlines() if line.strip()]

    assert len(output_rows) == 1
    assert output_rows[0]["scene_eligible"] is True
    assert Path(output_rows[0]["latent_path"]).exists()
    assert any(row["asset_id"] == "objaverse_bench_uid_script" for row in appended_rows)
    assert report["append_manifest_new_rows"] == 1
    assert report["placeholder_latent_count"] == 1
    assert Path(report["report_out"]).exists()


def test_run_objaverse_import_can_rebuild_index(monkeypatch, tmp_path: Path):
    mesh_path = (tmp_path / "cache" / "asset.glb").resolve()
    mesh_path.parent.mkdir(parents=True, exist_ok=True)
    mesh_path.write_bytes(b"glb")
    candidate = ObjaverseCandidate(
        uid="uid_rebuild",
        roadgen_category="lamp",
        lvis_category="streetlight",
        score=2.0,
        annotation=_annotation(uid="uid_rebuild", name="Street light"),
        reasons=("lvis:streetlight",),
    )
    row = compose_manifest_row(
        candidate,
        mesh_path=str(mesh_path),
        latents_dir=tmp_path / "latents",
        split="train",
    )
    fake_result = ObjaverseImportResult(
        manifest_rows=(row,),
        selected_candidates=(candidate,),
        downloaded_paths={"uid_rebuild": str(mesh_path)},
        cache_root=(tmp_path / "cache").resolve(),
        report={"selected_by_category": {"lamp": 1}},
    )
    monkeypatch.setattr(objaverse_script, "import_objaverse_assets", lambda **kwargs: fake_result)
    monkeypatch.setattr(
        objaverse_script.production_seed,
        "rebuild_real_index",
        lambda **kwargs: {"asset_count": 9, "embedding_dim": 512, "assets_pipeline_path": "x"},
    )

    report = objaverse_script.run_objaverse_import(
        cache_root=tmp_path / "cache",
        output_manifest=tmp_path / "objaverse_assets_manifest.jsonl",
        latents_dir=tmp_path / "latents",
        requested_categories=["lamp"],
        max_per_category=1,
        download_processes=1,
        split="train",
        clean_manifest=True,
        report_out=None,
        append_manifest=None,
        rebuild_index=True,
        artifacts_dir=tmp_path / "artifacts",
        model_name="openai/clip-vit-base-patch32",
        model_dir=None,
        local_files_only=True,
        device="cpu",
    )

    assert report["rebuild_index"] is True
    assert report["index_summary"]["asset_count"] == 9


def test_append_manifest_rows_replaces_duplicate_asset_id(tmp_path: Path):
    manifest = tmp_path / "manifest.jsonl"
    initial = {
        "asset_id": "dup_asset",
        "category": "bench",
        "text_desc": "old",
        "mesh_path": "/tmp/old.glb",
        "latent_path": "/tmp/old.pt",
        "license": "cc-by",
        "source": "old",
        "split": "train",
    }
    manifest.write_text(json.dumps(initial, ensure_ascii=True) + "\n", encoding="utf-8")

    appended = append_manifest_rows(
        manifest,
        [
            {
                "asset_id": "dup_asset",
                "category": "bench",
                "text_desc": "new",
                "mesh_path": "/tmp/new.glb",
                "latent_path": "/tmp/new.pt",
                "license": "cc-by",
                "source": "new",
                "split": "train",
            }
        ],
    )

    rows = [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert appended == 0
    assert len(rows) == 1
    assert rows[0]["source"] == "new"
