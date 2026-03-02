"""Learned policy model for slot-level asset selection (M4)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .layout_features import DEFAULT_POLICY_INPUT_DIM

try:
    import torch
    import torch.nn as nn
except ImportError:
    torch = None
    nn = None


@dataclass(frozen=True)
class PolicyTrainConfig:
    epochs: int = 20
    batch_size: int = 256
    lr: float = 1e-3
    weight_decay: float = 1e-4
    entropy_weight: float = 0.01
    patience: int = 3
    device: str = "cpu"


class LayoutPolicyMLP(nn.Module if nn is not None else object):
    """Simple candidate scoring MLP for slot selection."""

    def __init__(
        self,
        input_dim: int = DEFAULT_POLICY_INPUT_DIM,
        hidden_dim: int = 64,
        hidden_dim2: int = 32,
        dropout: float = 0.1,
    ) -> None:
        if nn is None:
            raise RuntimeError("torch is required for layout policy model")
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(int(input_dim), int(hidden_dim)),
            nn.ReLU(),
            nn.Dropout(float(dropout)),
            nn.Linear(int(hidden_dim), int(hidden_dim2)),
            nn.ReLU(),
            nn.Dropout(float(dropout)),
            nn.Linear(int(hidden_dim2), 1),
        )

    def forward(self, x):
        return self.net(x)


class LayoutPolicyRuntime:
    """Runtime scorer for learned layout policy checkpoints."""

    def __init__(self, model: LayoutPolicyMLP, device: str = "cpu") -> None:
        self.model = model
        self.device = device

    @classmethod
    def from_checkpoint(cls, ckpt_path: Path, device: str = "cpu") -> "LayoutPolicyRuntime":
        if torch is None:
            raise RuntimeError("torch is required for learned layout policy runtime")
        ckpt_path = Path(ckpt_path).resolve()
        if not ckpt_path.exists():
            raise FileNotFoundError(f"Policy checkpoint not found: {ckpt_path}")

        try:
            payload = torch.load(ckpt_path, map_location="cpu", weights_only=False)
        except TypeError:
            payload = torch.load(ckpt_path, map_location="cpu")
        input_dim = int(payload.get("input_dim", DEFAULT_POLICY_INPUT_DIM))
        hidden_dim = int(payload.get("hidden_dim", 64))
        hidden_dim2 = int(payload.get("hidden_dim2", 32))
        dropout = float(payload.get("dropout", 0.1))

        model = LayoutPolicyMLP(
            input_dim=input_dim,
            hidden_dim=hidden_dim,
            hidden_dim2=hidden_dim2,
            dropout=dropout,
        )
        state_dict = payload.get("state_dict", payload)
        model.load_state_dict(state_dict)
        model.eval()
        model.to(torch.device(device))
        return cls(model=model, device=device)

    def score_candidates(self, features: np.ndarray) -> np.ndarray:
        """Score [N, D] features into logits [N]."""
        if torch is None:
            raise RuntimeError("torch is required for learned layout policy runtime")
        if features.size == 0:
            return np.zeros((0,), dtype=np.float32)
        with torch.no_grad():
            x = torch.as_tensor(features, dtype=torch.float32, device=torch.device(self.device))
            logits = self.model(x).squeeze(-1)
            return logits.detach().cpu().numpy().astype(np.float32)


def split_samples_by_scene(
    samples: Sequence[Dict[str, object]],
    train_ratio: float = 0.9,
) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    """Stable split by scene_id hash so all candidates in one scene stay together."""
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


def _slot_logits_and_targets(model, batch_samples: Sequence[Dict[str, object]], device):
    if torch is None:
        raise RuntimeError("torch is required for layout policy training")
    logits_list = []
    targets = []
    for sample in batch_samples:
        feats = np.asarray(sample["candidate_features"], dtype=np.float32)
        chosen_index = int(sample["chosen_index"])
        if feats.ndim != 2 or feats.shape[0] <= 0:
            continue
        if chosen_index < 0 or chosen_index >= feats.shape[0]:
            continue
        x = torch.as_tensor(feats, dtype=torch.float32, device=device)
        logits = model(x).squeeze(-1)
        logits_list.append(logits)
        targets.append(chosen_index)
    return logits_list, targets


def train_layout_policy(
    train_samples: Sequence[Dict[str, object]],
    val_samples: Sequence[Dict[str, object]],
    out_dir: Path,
    config: PolicyTrainConfig,
    resume_checkpoint: Path | None = None,
    progress_callback: Optional[Callable[[Dict[str, float]], None]] = None,
) -> Dict[str, object]:
    """Train MLP using per-slot candidate CE + entropy regularization."""
    if torch is None:
        raise RuntimeError("torch is required for layout policy training")
    import torch.nn.functional as F

    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    device = torch.device(config.device)
    model = LayoutPolicyMLP(input_dim=DEFAULT_POLICY_INPUT_DIM).to(device)
    resumed_from = ""
    if resume_checkpoint is not None:
        resume_path = Path(resume_checkpoint).expanduser().resolve()
        if resume_path.exists():
            try:
                payload = torch.load(resume_path, map_location="cpu", weights_only=False)
            except TypeError:
                payload = torch.load(resume_path, map_location="cpu")
            state_dict = payload.get("state_dict", payload)
            model.load_state_dict(state_dict, strict=False)
            resumed_from = str(resume_path)
    optimizer = torch.optim.AdamW(
        model.parameters(),
        lr=float(config.lr),
        weight_decay=float(config.weight_decay),
    )

    best_val = float("inf")
    best_state = None
    no_improve = 0
    curve: List[Dict[str, float]] = []

    train_list = list(train_samples)
    val_list = list(val_samples)

    for epoch in range(int(config.epochs)):
        model.train()
        rng = np.random.default_rng(2026 + epoch)
        rng.shuffle(train_list)

        train_losses: List[float] = []
        step = max(1, int(config.batch_size))
        for i in range(0, len(train_list), step):
            batch = train_list[i : i + step]
            logits_list, targets = _slot_logits_and_targets(model, batch, device=device)
            if not logits_list:
                continue

            ce_terms = []
            entropy_terms = []
            for logits, target_idx in zip(logits_list, targets):
                ce_terms.append(F.cross_entropy(logits.unsqueeze(0), torch.tensor([target_idx], device=device)))
                probs = torch.softmax(logits, dim=0)
                entropy = -(probs * torch.log(probs + 1e-8)).sum()
                entropy_terms.append(entropy)

            ce_loss = torch.stack(ce_terms).mean()
            entropy_loss = torch.stack(entropy_terms).mean()
            loss = ce_loss - float(config.entropy_weight) * entropy_loss

            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            train_losses.append(float(ce_loss.detach().cpu().item()))

        model.eval()
        val_losses: List[float] = []
        with torch.no_grad():
            logits_list, targets = _slot_logits_and_targets(model, val_list, device=device)
            for logits, target_idx in zip(logits_list, targets):
                val_losses.append(
                    float(F.cross_entropy(logits.unsqueeze(0), torch.tensor([target_idx], device=device)).item())
                )

        train_loss = float(np.mean(train_losses)) if train_losses else 0.0
        val_loss = float(np.mean(val_losses)) if val_losses else train_loss
        curve.append({"epoch": float(epoch + 1), "train_loss": train_loss, "val_loss": val_loss})
        if progress_callback is not None:
            progress_callback(
                {
                    "epoch": float(epoch + 1),
                    "train_loss": float(train_loss),
                    "val_loss": float(val_loss),
                    "best_val_loss_so_far": float(min(best_val, val_loss)),
                    "no_improve": float(no_improve),
                }
            )

        if val_loss < best_val - 1e-6:
            best_val = val_loss
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
            no_improve = 0
        else:
            no_improve += 1
            if no_improve >= int(config.patience):
                break

    if best_state is None:
        best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}

    ckpt_path = out_dir / "layout_policy.pt"
    payload = {
        "state_dict": best_state,
        "input_dim": DEFAULT_POLICY_INPUT_DIM,
        "hidden_dim": 64,
        "hidden_dim2": 32,
        "dropout": 0.1,
        "best_val_loss": float(best_val),
    }
    torch.save(payload, ckpt_path)

    meta = {
        "train_size": len(train_list),
        "val_size": len(val_list),
        "best_val_loss": float(best_val),
        "epochs_ran": len(curve),
        "checkpoint": str(ckpt_path),
        "resumed_from": resumed_from,
    }
    meta_path = out_dir / "layout_policy_meta.json"
    curve_path = out_dir / "train_curve.json"
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=True), encoding="utf-8")
    curve_path.write_text(json.dumps(curve, indent=2, ensure_ascii=True), encoding="utf-8")

    return {
        "checkpoint": str(ckpt_path),
        "meta_path": str(meta_path),
        "curve_path": str(curve_path),
        "meta": meta,
        "curve": curve,
    }
