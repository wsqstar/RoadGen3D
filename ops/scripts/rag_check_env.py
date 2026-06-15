#!/usr/bin/env python3
"""Check local environment readiness for milestone-1."""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict


def package_status(module_name: str) -> Dict[str, object]:
    spec = importlib.util.find_spec(module_name)
    if spec is None:
        return {"installed": False, "version": None}
    try:
        module = importlib.import_module(module_name)
        version = getattr(module, "__version__", None)
        return {"installed": True, "version": str(version) if version is not None else "unknown"}
    except Exception as exc:
        return {"installed": True, "version": "unknown", "import_error": str(exc)}


def generate_report() -> Dict[str, object]:
    packages = {
        "numpy": package_status("numpy"),
        "torch": package_status("torch"),
        "transformers": package_status("transformers"),
        "faiss": package_status("faiss"),
        "pytest": package_status("pytest"),
    }

    torch_info: Dict[str, object] = {"installed": packages["torch"]["installed"]}
    if packages["torch"]["installed"]:
        import torch

        torch_info.update(
            {
                "cuda_available": bool(torch.cuda.is_available()),
                "cuda_device_count": int(torch.cuda.device_count()) if torch.cuda.is_available() else 0,
                "mps_available": bool(getattr(torch.backends, "mps", None) and torch.backends.mps.is_available()),
            }
        )
    else:
        torch_info.update({"cuda_available": False, "cuda_device_count": 0, "mps_available": False})

    return {
        "timestamp_utc": datetime.now(tz=timezone.utc).isoformat(),
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "executable": sys.executable,
        "packages": packages,
        "torch": torch_info,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate an environment readiness report.")
    parser.add_argument("--out", type=Path, default=Path("artifacts/m1/env_report.json"), help="Output JSON path.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = generate_report()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=True), encoding="utf-8")
    print(f"Wrote environment report to: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
