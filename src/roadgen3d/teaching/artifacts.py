"""Project-scoped artifact storage with local and S3-compatible backends."""

from __future__ import annotations

import io
import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO


ROOT = Path(__file__).resolve().parents[3]


def safe_object_key(project_id: str, artifact_id: str, filename: str) -> str:
    cleaned = Path(str(filename or "artifact.bin")).name.replace(" ", "_")
    key = PurePosixPath("projects", project_id, "artifacts", artifact_id, cleaned)
    if ".." in key.parts:
        raise ValueError("Artifact key escaped its project namespace.")
    return str(key)


@dataclass(frozen=True)
class StoredObject:
    key: str
    byte_size: int


class ArtifactStore:
    def put(self, key: str, data: bytes, *, media_type: str) -> StoredObject:
        raise NotImplementedError

    def open(self, key: str) -> BinaryIO:
        raise NotImplementedError

    def delete(self, key: str) -> None:
        raise NotImplementedError


class LocalArtifactStore(ArtifactStore):
    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root or os.getenv("ROADGEN_ARTIFACT_ROOT") or ROOT / "artifacts" / "teaching" / "objects").resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("Artifact key escaped the configured object root.") from exc
        return path

    def put(self, key: str, data: bytes, *, media_type: str) -> StoredObject:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_bytes(data)
        temporary.replace(path)
        return StoredObject(key=key, byte_size=len(data))

    def open(self, key: str) -> BinaryIO:
        return self._path(key).open("rb")

    def delete(self, key: str) -> None:
        path = self._path(key)
        if path.is_file():
            path.unlink()


class S3ArtifactStore(ArtifactStore):
    def __init__(self) -> None:
        import boto3

        self.bucket = os.getenv("ROADGEN_S3_BUCKET", "roadgen3d")
        self.client = boto3.client(
            "s3",
            endpoint_url=os.getenv("ROADGEN_S3_ENDPOINT") or None,
            aws_access_key_id=os.getenv("ROADGEN_S3_ACCESS_KEY") or None,
            aws_secret_access_key=os.getenv("ROADGEN_S3_SECRET_KEY") or None,
            region_name=os.getenv("ROADGEN_S3_REGION", "us-east-1"),
        )
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except Exception:
            self.client.create_bucket(Bucket=self.bucket)

    def put(self, key: str, data: bytes, *, media_type: str) -> StoredObject:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=media_type)
        return StoredObject(key=key, byte_size=len(data))

    def open(self, key: str) -> BinaryIO:
        payload = self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        return io.BytesIO(payload)

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)


def create_artifact_store() -> ArtifactStore:
    if os.getenv("ROADGEN_OBJECT_STORE", "local").strip().lower() == "s3":
        return S3ArtifactStore()
    return LocalArtifactStore()

