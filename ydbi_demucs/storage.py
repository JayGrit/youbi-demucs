from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from minio import Minio
from minio.error import S3Error

from .config import (
    MINIO_ACCESS_KEY,
    MINIO_BUCKET,
    MINIO_ENDPOINT,
    MINIO_FULL_BASE_URL,
    MINIO_SECRET_KEY,
    MINIO_SECURE,
    STORAGE_BACKEND,
)


@dataclass(frozen=True)
class ObjectRef:
    bucket: str
    object_name: str


def _endpoint_parts() -> tuple[str, bool]:
    parsed = urlparse(MINIO_ENDPOINT)
    if parsed.scheme:
        return parsed.netloc, parsed.scheme == "https"
    return MINIO_ENDPOINT, MINIO_SECURE


def _minio_client() -> Minio:
    endpoint, secure = _endpoint_parts()
    return Minio(endpoint, access_key=MINIO_ACCESS_KEY, secret_key=MINIO_SECRET_KEY, secure=secure)


def _public_read_policy(bucket: str) -> str:
    return json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"AWS": ["*"]},
                    "Action": ["s3:GetObject"],
                    "Resource": [f"arn:aws:s3:::{bucket}/*"],
                }
            ],
        }
    )


def _ensure_bucket(client: Minio, bucket: str) -> None:
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)
    client.set_bucket_policy(bucket, _public_read_policy(bucket))


def object_url(object_name: str, bucket: str = MINIO_BUCKET) -> str:
    return f"{MINIO_FULL_BASE_URL.rstrip('/')}/{bucket}/{object_name.lstrip('/')}"


def upload(local_path: Path, object_name: str, content_type: str = "application/octet-stream") -> str:
    if not local_path.exists() or local_path.stat().st_size == 0:
        raise FileNotFoundError(f"output does not exist or is empty: {local_path}")

    if STORAGE_BACKEND != "minio":
        return f"local:{local_path}"

    client = _minio_client()
    _ensure_bucket(client, MINIO_BUCKET)
    normalized = object_name.lstrip("/")
    client.fput_object(MINIO_BUCKET, normalized, str(local_path), content_type=content_type)
    return object_url(normalized)


def parse_object_ref(ref: str) -> ObjectRef | None:
    value = str(ref or "").strip()
    if not value:
        return None

    parsed = urlparse(value)
    if parsed.scheme == "http" and parsed.hostname == "120.53.92.66" and parsed.port == 9000:
        prefix = f"/{MINIO_BUCKET}/"
        if not parsed.path.startswith(prefix):
            return None
        object_name = parsed.path[len(prefix) :]
        return ObjectRef(MINIO_BUCKET, object_name) if object_name else None

    return None


def download(ref: str | Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source = str(ref)
    local = Path(source.removeprefix("local:"))
    if local.exists() and local.stat().st_size > 0:
        if local.resolve() != destination.resolve():
            shutil.copy2(local, destination)
        return destination

    object_info = parse_object_ref(source)
    if not object_info:
        raise FileNotFoundError(f"input does not exist locally or in minio: {source}")

    try:
        client = _minio_client()
        client.fget_object(object_info.bucket, object_info.object_name, str(destination))
        return destination
    except S3Error as exc:
        raise FileNotFoundError(
            f"input does not exist locally or in minio: {source}; tried "
            f"{object_url(object_info.object_name, object_info.bucket)}: {exc.code}"
        ) from exc
