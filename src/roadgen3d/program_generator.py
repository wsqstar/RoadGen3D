"""Heuristic and learned StreetProgram generation runtimes."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from .poi_taxonomy import asset_category_for_poi
from .street_priors import DEFAULT_CATEGORIES
from .street_program import infer_street_program
from .spatial_features import (
    build_spatial_context,
    compute_scene_distance_stats,
    vectorize_scene_stats,
    SCENE_STATS_DIM,
)
from .types import ProgramGenerationInput, ProgramGenerationResult, StreetBand, StreetProgram

ROAD_TYPE_VOCAB: Tuple[str, ...] = (
    "mixed_use",
    "residential",
    "urban_core",
    "industrial",
    "transit_corridor",
    "boulevard",
)
CROSS_SECTION_VOCAB: Tuple[str, ...] = (
    "balanced_complete_street",
    "pedestrian_priority",
    "transit_priority",
)
GOAL_VOCAB: Tuple[str, ...] = (
    "safety",
    "walkability",
    "amenity",
    "clarity",
    "greening",
    "transit_access",
    "comfort",
    "legibility",
    "throughput",
)
RESERVED_RIGHT_VOCAB: Tuple[str, ...] = ("none",) + DEFAULT_CATEGORIES
PROGRAM_FEATURE_DIM = 54

try:
    import torch
    import torch.nn as nn
    import torch.nn.functional as F
except ImportError:
    torch = None
    nn = None
    F = None


def _hashed_features(text: str, dims: int) -> List[float]:
    digest = hashlib.md5(text.encode("utf-8")).digest()
    values: List[float] = []
    for idx in range(dims):
        values.append(float(digest[idx % len(digest)]) / 255.0)
    return values


def _one_hot(value: str, vocab: Sequence[str]) -> List[float]:
    return [1.0 if str(value) == item else 0.0 for item in vocab]


def vectorize_program_input(data: ProgramGenerationInput) -> np.ndarray:
    """Convert generation inputs into a fixed-width feature vector."""

    features: List[float] = []
    cfg = data.compose_config
    features.extend(
        [
            float(cfg.length_m) / 100.0,
            float(cfg.road_width_m) / 20.0,
            float(cfg.sidewalk_width_m) / 10.0,
            float(cfg.lane_count) / 6.0,
            float(cfg.density) / 3.0,
            1.0 if str(cfg.layout_mode) == "osm" else 0.0,
            1.0 if str(cfg.constraint_mode) == "soft" else 0.0,
        ]
    )
    features.extend(_hashed_features(str(data.query), 8))
    features.extend(_hashed_features(str(cfg.city_context), 4))
    features.extend(_hashed_features(str(cfg.target_street_type), 3))
    features.extend(_one_hot(str(data.constraint_profile), ("balanced_complete_street_v1", "pedestrian_priority_v1", "transit_priority_v1", "noise_aware_v1")))
    available = set(data.available_categories)
    features.extend([1.0 if category in available else 0.0 for category in DEFAULT_CATEGORIES])
    inventory = data.inventory_summary.category_counts if data.inventory_summary is not None else {}
    features.extend([min(float(inventory.get(category, 0)) / 10.0, 1.0) for category in DEFAULT_CATEGORIES])
    graph = data.road_segment_graph
    if graph is not None and hasattr(graph, "summary"):
        summary = graph.summary()
        features.extend(
            [
                min(float(summary.get("segment_count", 0.0)) / 100.0, 1.0),
                min(float(summary.get("edge_count", 0.0)) / 100.0, 1.0),
                min(float(summary.get("junction_segment_count", 0.0)) / 50.0, 1.0),
                min(float(summary.get("avg_segment_length_m", 0.0)) / 30.0, 1.0),
            ]
        )
    else:
        features.extend([0.0, 0.0, 0.0, 0.0])
    # --- spatial distance statistics (M8) ---
    spatial_ctx = build_spatial_context(
        cfg,
        road_segment_graph=data.road_segment_graph,
        poi_context=getattr(data, "poi_context", None),
    )
    scene_stats = compute_scene_distance_stats(spatial_ctx)
    features.extend(vectorize_scene_stats(scene_stats).tolist())
    arr = np.asarray(features, dtype=np.float32)
    if arr.shape[0] != PROGRAM_FEATURE_DIM:
        raise RuntimeError(f"program feature dim mismatch: got {arr.shape[0]}, expected {PROGRAM_FEATURE_DIM}")
    return arr


def _extract_band_widths(program: StreetProgram) -> Tuple[float, float, float, float]:
    by_name = {band.name: band for band in program.bands}
    left_furnishing = float(by_name.get("left_furnishing", StreetBand("", "", "", 0.0, 0.0)).width_m)
    left_clear = float(by_name.get("left_clear_path", StreetBand("", "", "", 0.0, 0.0)).width_m)
    right_clear = float(by_name.get("right_clear_path", StreetBand("", "", "", 0.0, 0.0)).width_m)
    right_edge = float(
        by_name.get(
            "right_transit_edge",
            by_name.get("right_furnishing", StreetBand("", "", "", 0.0, 0.0)),
        ).width_m
    )
    return left_furnishing, left_clear, right_clear, right_edge


def program_to_targets(program: StreetProgram) -> Dict[str, np.ndarray]:
    reserved_right = str(program.reserved_band_categories.get("right_transit_edge", "none"))
    if reserved_right not in RESERVED_RIGHT_VOCAB:
        reserved_right = "none"
    goal_weights = [float(program.design_goal_weights.get(goal, 0.0)) for goal in GOAL_VOCAB]
    counts = [float(program.furniture_requirements.get(category, 0)) for category in DEFAULT_CATEGORIES]
    return {
        "road_type": np.asarray([ROAD_TYPE_VOCAB.index(program.road_type) if program.road_type in ROAD_TYPE_VOCAB else 0], dtype=np.int64),
        "cross_section": np.asarray([CROSS_SECTION_VOCAB.index(program.cross_section_type) if program.cross_section_type in CROSS_SECTION_VOCAB else 0], dtype=np.int64),
        "lane_count": np.asarray([max(0, min(int(program.lane_count) - 1, 5))], dtype=np.int64),
        "band_widths": np.asarray(_extract_band_widths(program), dtype=np.float32),
        "category_counts": np.asarray(counts, dtype=np.float32),
        "reserved_right": np.asarray([RESERVED_RIGHT_VOCAB.index(reserved_right)], dtype=np.int64),
        "goal_weights": np.asarray(goal_weights, dtype=np.float32),
    }


def _recompute_band_centers(bands: Sequence[StreetBand]) -> Tuple[StreetBand, ...]:
    road_band = next((band for band in bands if band.kind == "carriageway"), None)
    road_half = float(road_band.width_m) / 2.0 if road_band is not None else 0.0
    left_offset = road_half
    right_offset = road_half
    rebuilt: List[StreetBand] = []
    for band in bands:
        if band.side == "left":
            rebuilt.append(
                StreetBand(
                    name=band.name,
                    kind=band.kind,
                    side=band.side,
                    width_m=float(band.width_m),
                    z_center_m=float(left_offset + float(band.width_m) / 2.0),
                    allowed_categories=band.allowed_categories,
                )
            )
            left_offset += float(band.width_m)
        elif band.side == "right":
            rebuilt.append(
                StreetBand(
                    name=band.name,
                    kind=band.kind,
                    side=band.side,
                    width_m=float(band.width_m),
                    z_center_m=float(-(right_offset + float(band.width_m) / 2.0)),
                    allowed_categories=band.allowed_categories,
                )
            )
            right_offset += float(band.width_m)
        else:
            rebuilt.append(
                StreetBand(
                    name=band.name,
                    kind=band.kind,
                    side=band.side,
                    width_m=float(band.width_m),
                    z_center_m=0.0,
                    allowed_categories=band.allowed_categories,
                )
            )
    return tuple(rebuilt)


def _normalize_goal_weights(weights: Dict[str, float]) -> Dict[str, float]:
    positive = {key: max(0.0, float(value)) for key, value in weights.items() if float(value) > 0.0}
    total = sum(positive.values())
    if total <= 0.0:
        return {}
    return {key: float(value / total) for key, value in positive.items()}


def _apply_prediction_to_program(base: StreetProgram, prediction: Dict[str, np.ndarray], available_categories: Iterable[str]) -> StreetProgram:
    road_type_idx = int(prediction["road_type"])
    cross_idx = int(prediction["cross_section"])
    lane_count = max(1, min(int(prediction["lane_count"]) + 1, 6))
    band_widths = np.asarray(prediction["band_widths"], dtype=np.float32)
    category_counts = np.asarray(prediction["category_counts"], dtype=np.float32)
    reserved_idx = int(prediction["reserved_right"])
    goal_weights_pred = np.asarray(prediction["goal_weights"], dtype=np.float32)

    updated_bands: List[StreetBand] = []
    for band in base.bands:
        width = float(band.width_m)
        if band.name == "left_furnishing":
            width = max(0.6, float(band_widths[0]))
        elif band.name == "left_clear_path":
            width = max(1.5, float(band_widths[1]))
        elif band.name == "right_clear_path":
            width = max(1.5, float(band_widths[2]))
        elif band.name in {"right_furnishing", "right_transit_edge"}:
            width = max(0.6, float(band_widths[3]))
        updated_bands.append(
            StreetBand(
                name=band.name,
                kind=band.kind,
                side=band.side,
                width_m=width,
                z_center_m=band.z_center_m,
                allowed_categories=band.allowed_categories,
            )
        )
    updated_bands = list(_recompute_band_centers(updated_bands))

    available = set(available_categories)
    predicted_counts: Dict[str, int] = {}
    for idx, category in enumerate(DEFAULT_CATEGORIES):
        if category not in available:
            continue
        predicted_counts[category] = max(0, int(round(float(category_counts[idx]))))

    right_reserved = RESERVED_RIGHT_VOCAB[max(0, min(reserved_idx, len(RESERVED_RIGHT_VOCAB) - 1))]
    reserved_band_categories = dict(base.reserved_band_categories)
    if right_reserved == "none":
        reserved_band_categories.pop("right_transit_edge", None)
    else:
        reserved_band_categories["right_transit_edge"] = right_reserved

    normalized_goal_weights = _normalize_goal_weights(
        {GOAL_VOCAB[idx]: float(goal_weights_pred[idx]) for idx in range(len(GOAL_VOCAB))}
    )
    ordered_goals = tuple(sorted(normalized_goal_weights.keys(), key=lambda key: normalized_goal_weights[key], reverse=True))
    if not ordered_goals:
        ordered_goals = base.design_goals
        normalized_goal_weights = dict(base.design_goal_weights)

    carriageway_width = next((float(band.width_m) for band in updated_bands if band.kind == "carriageway"), float(base.road_width_m))
    clear_widths = [float(band.width_m) for band in updated_bands if band.kind == "clear_path"]
    furnishing_widths = [float(band.width_m) for band in updated_bands if band.kind in {"furnishing", "transit_edge"}]
    notes = tuple(dict.fromkeys(base.notes + ("learned_program_generator_v1",)))
    observed_poi_counts = dict(base.observed_poi_counts)
    for poi_type, count in observed_poi_counts.items():
        category = asset_category_for_poi(poi_type)
        if category is None:
            continue
        predicted_counts[category] = max(int(predicted_counts.get(category, 0)), int(count))
    control_points = list(base.control_points)
    if (
        int(observed_poi_counts.get("bus_stop", 0)) > 0
        or int(observed_poi_counts.get("subway_entrance", 0)) > 0
    ) and "transit_stop" not in control_points:
        control_points.append("transit_stop")
    if int(observed_poi_counts.get("crossing", 0)) > 0 and "crossing" not in control_points:
        control_points.append("crossing")
    if int(observed_poi_counts.get("parking_entrance", 0)) > 0 and "access" not in control_points:
        control_points.append("access")
    if (
        int(observed_poi_counts.get("bus_stop", 0)) > 0
        or int(observed_poi_counts.get("subway_entrance", 0)) > 0
    ) and "transit_access" not in ordered_goals:
        ordered_goals = tuple(list(ordered_goals) + ["transit_access"])

    return StreetProgram(
        query=base.query,
        road_type=ROAD_TYPE_VOCAB[max(0, min(road_type_idx, len(ROAD_TYPE_VOCAB) - 1))],
        city_context=base.city_context,
        target_standard=base.target_standard,
        lane_count=lane_count,
        cross_section_type=CROSS_SECTION_VOCAB[max(0, min(cross_idx, len(CROSS_SECTION_VOCAB) - 1))],
        road_width_m=carriageway_width,
        sidewalk_width_m=max(clear_widths) if clear_widths else base.sidewalk_width_m,
        furnishing_width_m=max(furnishing_widths) if furnishing_widths else base.furnishing_width_m,
        bands=tuple(updated_bands),
        furniture_requirements=predicted_counts,
        control_points=tuple(control_points),
        design_goals=ordered_goals,
        context_conditions=dict(base.context_conditions),
        observed_poi_counts=observed_poi_counts,
        reserved_band_categories=reserved_band_categories,
        design_goal_weights=normalized_goal_weights,
        notes=notes,
    )


@dataclass(frozen=True)
class ProgramTrainConfig:
    epochs: int = 60
    batch_size: int = 32
    lr: float = 5e-4
    weight_decay: float = 1e-4
    patience: int = 5
    device: str = "cpu"


class ProgramGeneratorMLP(nn.Module if nn is not None else object):
    """Shared MLP with multi-head outputs for structured StreetProgram prediction."""

    def __init__(self, input_dim: int = PROGRAM_FEATURE_DIM, hidden_dim: int = 96, dropout: float = 0.1) -> None:
        if nn is None:
            raise RuntimeError("torch is required for program generator training/runtime")
        super().__init__()
        self.backbone = nn.Sequential(
            nn.Linear(int(input_dim), int(hidden_dim)),
            nn.ReLU(),
            nn.Dropout(float(dropout)),
            nn.Linear(int(hidden_dim), int(hidden_dim)),
            nn.ReLU(),
            nn.Dropout(float(dropout)),
        )
        self.road_type = nn.Linear(int(hidden_dim), len(ROAD_TYPE_VOCAB))
        self.cross_section = nn.Linear(int(hidden_dim), len(CROSS_SECTION_VOCAB))
        self.lane_count = nn.Linear(int(hidden_dim), 6)
        self.band_widths = nn.Linear(int(hidden_dim), 4)
        self.category_counts = nn.Linear(int(hidden_dim), len(DEFAULT_CATEGORIES))
        self.reserved_right = nn.Linear(int(hidden_dim), len(RESERVED_RIGHT_VOCAB))
        self.goal_weights = nn.Linear(int(hidden_dim), len(GOAL_VOCAB))

    def forward(self, x):
        latent = self.backbone(x)
        return {
            "road_type": self.road_type(latent),
            "cross_section": self.cross_section(latent),
            "lane_count": self.lane_count(latent),
            "band_widths": self.band_widths(latent),
            "category_counts": self.category_counts(latent),
            "reserved_right": self.reserved_right(latent),
            "goal_weights": self.goal_weights(latent),
        }


class ProgramGeneratorRuntime:
    """Runtime dispatcher for heuristic and learned program generation backends."""

    def __init__(self, backend: str = "heuristic_v1", model: Optional[ProgramGeneratorMLP] = None, device: str = "cpu") -> None:
        self.backend = str(backend)
        self.model = model
        self.device = device

    @classmethod
    def from_checkpoint(cls, ckpt_path: Path, device: str = "cpu") -> "ProgramGeneratorRuntime":
        if torch is None:
            raise RuntimeError("torch is required for learned_v1 program generation")
        ckpt = Path(ckpt_path).expanduser().resolve()
        if not ckpt.exists():
            raise FileNotFoundError(f"Program generator checkpoint not found: {ckpt}")
        try:
            payload = torch.load(ckpt, map_location="cpu", weights_only=False)
        except TypeError:
            payload = torch.load(ckpt, map_location="cpu")
        model = ProgramGeneratorMLP(input_dim=int(payload.get("input_dim", PROGRAM_FEATURE_DIM)))
        state_dict = payload.get("state_dict", payload)
        model.load_state_dict(state_dict)
        model.eval()
        model.to(torch.device(device))
        return cls(backend="learned_v1", model=model, device=device)

    def generate(self, data: ProgramGenerationInput) -> ProgramGenerationResult:
        base_program = infer_street_program(
            data.compose_config,
            data.available_categories,
            poi_context=getattr(data, "poi_context", None),
        )
        requested = str(data.compose_config.program_generator).strip().lower()
        if self.backend != "learned_v1" or self.model is None:
            return ProgramGenerationResult(
                program=base_program,
                backend_requested=requested,
                backend_used="heuristic_v1",
                fallback_reason="" if requested == "heuristic_v1" else "learned_v1 checkpoint unavailable; fallback to heuristic_v1",
            )
        if torch is None:
            return ProgramGenerationResult(
                program=base_program,
                backend_requested=requested,
                backend_used="heuristic_v1",
                fallback_reason="torch unavailable; fallback to heuristic_v1",
            )

        vec = vectorize_program_input(data)
        # Dimension compatibility: old checkpoint may expect different input_dim
        model_input_dim = self.model.backbone[0].in_features
        if vec.shape[0] != model_input_dim:
            import warnings
            warnings.warn(
                f"Program generator feature dim mismatch: model expects {model_input_dim}, "
                f"got {vec.shape[0]}. Falling back to heuristic_v1.",
                stacklevel=2,
            )
            return ProgramGenerationResult(
                program=base_program,
                backend_requested=requested,
                backend_used="heuristic_v1",
                fallback_reason=f"feature dim mismatch ({vec.shape[0]} vs {model_input_dim}); fallback to heuristic_v1",
            )
        with torch.no_grad():
            tensor = torch.as_tensor(vec[None, :], dtype=torch.float32, device=torch.device(self.device))
            outputs = self.model(tensor)
            prediction = {
                "road_type": int(torch.argmax(outputs["road_type"], dim=-1).item()),
                "cross_section": int(torch.argmax(outputs["cross_section"], dim=-1).item()),
                "lane_count": int(torch.argmax(outputs["lane_count"], dim=-1).item()),
                "band_widths": outputs["band_widths"].detach().cpu().numpy()[0],
                "category_counts": torch.relu(outputs["category_counts"]).detach().cpu().numpy()[0],
                "reserved_right": int(torch.argmax(outputs["reserved_right"], dim=-1).item()),
                "goal_weights": torch.softmax(outputs["goal_weights"], dim=-1).detach().cpu().numpy()[0],
            }
        learned_program = _apply_prediction_to_program(base_program, prediction, data.available_categories)
        return ProgramGenerationResult(
            program=learned_program,
            backend_requested=requested,
            backend_used="learned_v1",
            fallback_reason="",
        )


def split_program_samples_by_scene(
    samples: Sequence[Dict[str, object]],
    train_ratio: float = 0.9,
) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    train: List[Dict[str, object]] = []
    val: List[Dict[str, object]] = []
    threshold = max(0.0, min(float(train_ratio), 1.0))
    for item in samples:
        scene_id = str(item.get("scene_id", ""))
        digest = hashlib.md5(scene_id.encode("utf-8")).hexdigest()
        bucket = (int(digest[:8], 16) % 1000) / 1000.0
        (train if bucket < threshold else val).append(item)
    if not train and val:
        train, val = val, []
    return train, val


def _program_loss(model, batch: Sequence[Dict[str, object]], device) -> Tuple[object, Dict[str, float]]:
    if torch is None or F is None:
        raise RuntimeError("torch is required for program generator training")
    features = torch.as_tensor(np.stack([np.asarray(item["features"], dtype=np.float32) for item in batch]), dtype=torch.float32, device=device)
    outputs = model(features)
    road_type_target = torch.as_tensor([int(item["targets"]["road_type"][0]) for item in batch], dtype=torch.long, device=device)
    cross_target = torch.as_tensor([int(item["targets"]["cross_section"][0]) for item in batch], dtype=torch.long, device=device)
    lane_target = torch.as_tensor([int(item["targets"]["lane_count"][0]) for item in batch], dtype=torch.long, device=device)
    reserved_target = torch.as_tensor([int(item["targets"]["reserved_right"][0]) for item in batch], dtype=torch.long, device=device)
    band_target = torch.as_tensor(np.stack([np.asarray(item["targets"]["band_widths"], dtype=np.float32) for item in batch]), dtype=torch.float32, device=device)
    count_target = torch.as_tensor(np.stack([np.asarray(item["targets"]["category_counts"], dtype=np.float32) for item in batch]), dtype=torch.float32, device=device)
    goal_target = torch.as_tensor(np.stack([np.asarray(item["targets"]["goal_weights"], dtype=np.float32) for item in batch]), dtype=torch.float32, device=device)

    road_loss = F.cross_entropy(outputs["road_type"], road_type_target)
    cross_loss = F.cross_entropy(outputs["cross_section"], cross_target)
    lane_loss = F.cross_entropy(outputs["lane_count"], lane_target)
    reserved_loss = F.cross_entropy(outputs["reserved_right"], reserved_target)
    band_loss = F.mse_loss(outputs["band_widths"], band_target)
    count_loss = F.mse_loss(torch.relu(outputs["category_counts"]), count_target)
    goal_loss = F.mse_loss(torch.softmax(outputs["goal_weights"], dim=-1), goal_target)
    loss = road_loss + cross_loss + lane_loss + reserved_loss + band_loss + count_loss + goal_loss
    metrics = {
        "road_loss": float(road_loss.detach().cpu().item()),
        "cross_loss": float(cross_loss.detach().cpu().item()),
        "lane_loss": float(lane_loss.detach().cpu().item()),
        "reserved_loss": float(reserved_loss.detach().cpu().item()),
        "band_loss": float(band_loss.detach().cpu().item()),
        "count_loss": float(count_loss.detach().cpu().item()),
        "goal_loss": float(goal_loss.detach().cpu().item()),
    }
    return loss, metrics


def train_program_generator(
    train_samples: Sequence[Dict[str, object]],
    val_samples: Sequence[Dict[str, object]],
    out_dir: Path,
    config: ProgramTrainConfig,
    resume_checkpoint: Path | None = None,
    progress_callback=None,
) -> Dict[str, object]:
    if torch is None:
        raise RuntimeError("torch is required for program generator training")
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    device = torch.device(config.device)
    model = ProgramGeneratorMLP(input_dim=PROGRAM_FEATURE_DIM).to(device)
    resumed_from = ""
    if resume_checkpoint is not None:
        ckpt = Path(resume_checkpoint).expanduser().resolve()
        if ckpt.exists():
            try:
                payload = torch.load(ckpt, map_location="cpu", weights_only=False)
            except TypeError:
                payload = torch.load(ckpt, map_location="cpu")
            state = payload.get("state_dict", payload)
            # Filter out tensors whose shape doesn't match the current model
            # (e.g. old 45-dim checkpoint vs new 54-dim model).
            cur_state = model.state_dict()
            compat_state = {
                k: v for k, v in state.items()
                if k in cur_state and cur_state[k].shape == v.shape
            }
            skipped = set(state.keys()) - set(compat_state.keys())
            if skipped:
                import warnings
                warnings.warn(
                    f"Skipped {len(skipped)} checkpoint key(s) due to shape mismatch: "
                    f"{sorted(skipped)[:5]}{'...' if len(skipped) > 5 else ''}",
                    stacklevel=2,
                )
            model.load_state_dict(compat_state, strict=False)
            resumed_from = str(ckpt)
    optimizer = torch.optim.AdamW(model.parameters(), lr=float(config.lr), weight_decay=float(config.weight_decay))
    train_list = list(train_samples)
    val_list = list(val_samples)
    best_val = float("inf")
    best_state = None
    no_improve = 0
    curve: List[Dict[str, float]] = []

    for epoch in range(int(config.epochs)):
        rng = np.random.default_rng(2026 + epoch)
        rng.shuffle(train_list)
        train_losses: List[float] = []
        for offset in range(0, len(train_list), max(1, int(config.batch_size))):
            batch = train_list[offset : offset + max(1, int(config.batch_size))]
            if not batch:
                continue
            model.train()
            loss, _metrics = _program_loss(model, batch, device=device)
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            train_losses.append(float(loss.detach().cpu().item()))

        model.eval()
        val_loss = 0.0
        if val_list:
            with torch.no_grad():
                val_tensor, _metrics = _program_loss(model, val_list, device=device)
                val_loss = float(val_tensor.detach().cpu().item())
        train_loss = float(sum(train_losses) / len(train_losses)) if train_losses else 0.0
        if not val_list:
            val_loss = train_loss
        curve.append({"epoch": float(epoch + 1), "train_loss": train_loss, "val_loss": val_loss})
        if progress_callback is not None:
            progress_callback(
                {
                    "epoch": float(epoch + 1),
                    "train_loss": train_loss,
                    "val_loss": val_loss,
                    "best_val_loss_so_far": min(best_val, val_loss),
                }
            )
        if val_loss < best_val - 1e-6:
            best_val = val_loss
            best_state = {key: value.detach().cpu().clone() for key, value in model.state_dict().items()}
            no_improve = 0
        else:
            no_improve += 1
            if no_improve >= int(config.patience):
                break

    if best_state is None:
        best_state = {key: value.detach().cpu().clone() for key, value in model.state_dict().items()}
    ckpt_payload = {
        "input_dim": PROGRAM_FEATURE_DIM,
        "state_dict": best_state,
        "road_type_vocab": ROAD_TYPE_VOCAB,
        "cross_section_vocab": CROSS_SECTION_VOCAB,
        "goal_vocab": GOAL_VOCAB,
        "reserved_right_vocab": RESERVED_RIGHT_VOCAB,
        "best_val_loss": float(best_val),
        "resumed_from": resumed_from,
    }
    ckpt_path = out_dir / "program_generator.pt"
    torch.save(ckpt_payload, ckpt_path)
    meta = {
        "best_val_loss": float(best_val),
        "input_dim": PROGRAM_FEATURE_DIM,
        "curve_length": len(curve),
        "resumed_from": resumed_from,
    }
    meta_path = out_dir / "program_generator_meta.json"
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=True), encoding="utf-8")
    curve_path = out_dir / "program_generator_curve.json"
    curve_path.write_text(json.dumps(curve, indent=2, ensure_ascii=True), encoding="utf-8")
    return {
        "checkpoint": str(ckpt_path),
        "meta_path": str(meta_path),
        "curve_path": str(curve_path),
        "meta": meta,
        "curve": curve,
    }
