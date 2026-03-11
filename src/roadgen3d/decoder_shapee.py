"""Shape-E decoder adapter with optional fallback to placeholder decoder."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional, Tuple

import numpy as np
import warnings

from .decoder import PlaceholderVoxelDecoder
from .runtime_device import resolve_device_backend, resolve_torch_device


class ShapeEDecoderError(RuntimeError):
    """Raised when Shape-E decoding fails and no fallback is available."""


def _normalize_decode_output(result):
    if not isinstance(result, tuple):
        raise TypeError("decoder output must be a tuple")
    if len(result) == 3:
        prob, voxel, meta = result
    elif len(result) == 2:
        prob, voxel = result
        meta = {}
    else:
        raise TypeError("decoder output tuple must have length 2 or 3")
    if not isinstance(meta, dict):
        meta = {"meta": str(meta)}
    return np.asarray(prob), np.asarray(voxel), meta


class ShapeEDecoder:
    """
    Shape-E adapter.

    Notes:
    - If Shape-E runtime is not available or decoding fails, this decoder can
      fallback to `PlaceholderVoxelDecoder`.
    - If `strict=True`, failures raise `ShapeEDecoderError` instead.
    """

    def __init__(
        self,
        resolution: int = 64,
        threshold: float = 0.5,
        device: str = "auto",
        model_dir: Optional[Path] = None,
        strict: bool = False,
        fallback_decoder: Optional[PlaceholderVoxelDecoder] = None,
    ) -> None:
        if resolution <= 1:
            raise ValueError("resolution must be > 1")
        if not (0.0 < threshold < 1.0):
            raise ValueError("threshold must be in (0, 1)")
        self.resolution = int(resolution)
        self.threshold = float(threshold)
        self.device = resolve_device_backend(device)
        self.model_dir = Path(model_dir).resolve() if model_dir else None
        self.strict = bool(strict)
        self.fallback_decoder = fallback_decoder
        self._shapee_cache = None

    def _decode_mesh_with_shapee(self, latent):
        """
        Decode latent to a trimesh mesh.

        Current implementation prefers explicit mesh path latent sources:
        - dict with `mesh_path`
        - string/path to mesh file

        If Shape-E runtime is installed, it attempts a direct decode path.
        """
        try:
            import trimesh
        except ImportError as exc:
            raise ShapeEDecoderError("`trimesh` is required for Shape-E adapter.") from exc

        if isinstance(latent, dict) and "mesh_path" in latent:
            mesh_path = Path(str(latent["mesh_path"])).expanduser().resolve()
            if not mesh_path.exists():
                raise ShapeEDecoderError(f"mesh_path does not exist: {mesh_path}")
            mesh = trimesh.load(mesh_path, force="mesh")
            if mesh.is_empty:
                raise ShapeEDecoderError(f"Loaded mesh is empty: {mesh_path}")
            return mesh

        if isinstance(latent, (str, Path)):
            mesh_path = Path(str(latent)).expanduser().resolve()
            if mesh_path.exists():
                mesh = trimesh.load(mesh_path, force="mesh")
                if mesh.is_empty:
                    raise ShapeEDecoderError(f"Loaded mesh is empty: {mesh_path}")
                return mesh

        # Best-effort direct Shape-E decode path (optional runtime).
        try:
            import torch
            from shap_e.models.download import load_model
            from shap_e.models.nn.camera import DifferentiableCameraBatch, DifferentiableProjectiveCamera
            from shap_e.util.collections import AttrDict
        except Exception as exc:
            raise ShapeEDecoderError(
                "Shape-E runtime unavailable for direct latent decode. "
                "Install Shape-E dependencies or provide mesh_path-based latents."
            ) from exc

        def _create_pan_cameras(size: int, device):
            origins = []
            xs = []
            ys = []
            zs = []
            for theta in np.linspace(0.0, 2.0 * np.pi, num=20):
                z = np.array([np.sin(theta), np.cos(theta), -0.5], dtype=np.float32)
                z = z / np.sqrt(np.sum(z**2))
                origin = -z * 4.0
                x = np.array([np.cos(theta), -np.sin(theta), 0.0], dtype=np.float32)
                y = np.cross(z, x)
                origins.append(origin)
                xs.append(x)
                ys.append(y)
                zs.append(z)
            return DifferentiableCameraBatch(
                shape=(1, len(xs)),
                flat_camera=DifferentiableProjectiveCamera(
                    origin=torch.from_numpy(np.stack(origins, axis=0)).float().to(device),
                    x=torch.from_numpy(np.stack(xs, axis=0)).float().to(device),
                    y=torch.from_numpy(np.stack(ys, axis=0)).float().to(device),
                    z=torch.from_numpy(np.stack(zs, axis=0)).float().to(device),
                    width=size,
                    height=size,
                    x_fov=0.7,
                    y_fov=0.7,
                ),
            )

        def _decode_latent_mesh(xm, latent_tensor):
            decoded = xm.renderer.render_views(
                AttrDict(cameras=_create_pan_cameras(2, latent_tensor.device)),
                params=xm.encoder.bottleneck_to_params(latent_tensor[None]),
                options=AttrDict(rendering_mode="stf", render_with_direction=False),
            )
            return decoded.raw_meshes[0]

        if self._shapee_cache is None:
            load_kwargs = {}
            if self.model_dir is not None:
                load_kwargs["cache_dir"] = str(self.model_dir)
            with warnings.catch_warnings():
                warnings.filterwarnings(
                    "ignore",
                    message=r"`torch\.cuda\.amp\.custom_(fwd|bwd)\(args\.\.\.\)` is deprecated\.",
                    category=FutureWarning,
                )
                xm = load_model("transmitter", device=resolve_torch_device(self.device), **load_kwargs)
            self._shapee_cache = {"xm": xm}
        xm = self._shapee_cache["xm"]
        try:
            latent_tensor = torch.as_tensor(latent, dtype=torch.float32, device=resolve_torch_device(self.device))
            # Accept [N], [1, N], or higher-rank latent tensors by flattening to vector.
            latent_vector = latent_tensor.reshape(-1)
            latent_ctx = int(getattr(xm.encoder, "latent_ctx", 0) or 0)
            d_latent = int(getattr(xm.encoder, "d_latent", 0) or 0)
            if latent_ctx > 0:
                if latent_vector.numel() % latent_ctx != 0:
                    raise ShapeEDecoderError(
                        "Incompatible Shape-E latent size: "
                        f"got {int(latent_vector.numel())} values, expected a multiple of latent_ctx={latent_ctx}. "
                        "This usually means you are using M1 placeholder latents (e.g. [1,256]). "
                        "Please generate real Shape-E latents via scripts/m2_11_encode_shapee_latents.py."
                    )
                width = int(latent_vector.numel() // latent_ctx)
                if d_latent > 0 and width != d_latent:
                    raise ShapeEDecoderError(
                        "Incompatible Shape-E latent width: "
                        f"got {width}, expected d_latent={d_latent}. "
                        "Please regenerate latents with the same Shape-E transmitter model."
                    )
            mesh = _decode_latent_mesh(xm, latent_vector).tri_mesh()
            if mesh.is_empty:
                raise ShapeEDecoderError("Shape-E decoded an empty mesh.")
            return mesh
        except Exception as exc:
            raise ShapeEDecoderError(f"Shape-E direct decode failed: {exc}") from exc

    def _mesh_to_voxel(self, mesh) -> Tuple[np.ndarray, np.ndarray]:
        points = mesh.sample(max(20000, self.resolution * self.resolution * 2))
        if points.shape[0] == 0:
            raise ShapeEDecoderError("Decoded mesh has no sampleable surface points.")

        mins = points.min(axis=0)
        maxs = points.max(axis=0)
        span = np.maximum(maxs - mins, 1e-6)
        norm = (points - mins) / span
        idx = np.clip(np.round(norm * (self.resolution - 1)), 0, self.resolution - 1).astype(np.int32)

        occ = np.zeros((self.resolution, self.resolution, self.resolution), dtype=np.uint8)
        occ[idx[:, 0], idx[:, 1], idx[:, 2]] = 1

        # Simple smoothing approximation without extra dependencies.
        prob = occ.astype(np.float32)
        for axis in range(3):
            prob = (prob + np.roll(prob, 1, axis=axis) + np.roll(prob, -1, axis=axis)) / 3.0
        prob = np.clip(prob, 0.0, 1.0).astype(np.float32)
        voxel = (prob > self.threshold).astype(np.uint8)
        return prob, voxel

    def decode(self, latent):
        try:
            mesh = self._decode_mesh_with_shapee(latent)
            prob, voxel = self._mesh_to_voxel(mesh)
            meta: Dict[str, object] = {
                "decoder": "shapee",
                "resolution": self.resolution,
                "threshold": self.threshold,
                "mesh": mesh,
            }
            return prob, voxel, meta
        except Exception as exc:
            if self.strict or self.fallback_decoder is None:
                raise ShapeEDecoderError(str(exc)) from exc
            prob, voxel, base_meta = _normalize_decode_output(self.fallback_decoder.decode(latent))
            base_meta = dict(base_meta)
            base_meta["decoder"] = "shapee_fallback"
            base_meta["shapee_error"] = str(exc)
            return prob, voxel, base_meta
