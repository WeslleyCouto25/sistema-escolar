import base64
import hashlib
import mimetypes
import os
import re
import threading
from pathlib import Path
from urllib.parse import quote

import boto3
from botocore.config import Config

_client = None
_client_lock = threading.Lock()


class R2NotConfigured(RuntimeError):
    pass


def _env(name, *aliases, default=None):
    for key in (name, *aliases):
        value = os.getenv(key)
        if value not in (None, ""):
            return value
    return default


def bucket_name():
    return _env("R2_BUCKET_NAME", "CLOUDFLARE_R2_BUCKET")


def is_configured():
    return bool(
        bucket_name()
        and _env("R2_ACCESS_KEY_ID", "CLOUDFLARE_R2_ACCESS_KEY_ID")
        and _env("R2_SECRET_ACCESS_KEY", "CLOUDFLARE_R2_SECRET_ACCESS_KEY")
        and (_env("R2_ENDPOINT_URL") or _env("R2_ACCOUNT_ID", "CLOUDFLARE_ACCOUNT_ID"))
    )


def require_configured():
    if not is_configured():
        raise R2NotConfigured(
            "Cloudflare R2 não configurado. Defina R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, "
            "R2_SECRET_ACCESS_KEY e R2_BUCKET_NAME no Render."
        )


def endpoint_url():
    explicit = _env("R2_ENDPOINT_URL")
    if explicit:
        return explicit.rstrip("/")
    account_id = _env("R2_ACCOUNT_ID", "CLOUDFLARE_ACCOUNT_ID")
    if not account_id:
        raise R2NotConfigured("R2_ACCOUNT_ID não configurado.")
    return f"https://{account_id}.r2.cloudflarestorage.com"


def get_client():
    global _client
    require_configured()
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = boto3.client(
                    "s3",
                    endpoint_url=endpoint_url(),
                    aws_access_key_id=_env("R2_ACCESS_KEY_ID", "CLOUDFLARE_R2_ACCESS_KEY_ID"),
                    aws_secret_access_key=_env("R2_SECRET_ACCESS_KEY", "CLOUDFLARE_R2_SECRET_ACCESS_KEY"),
                    region_name="auto",
                    config=Config(
                        signature_version="s3v4",
                        retries={"max_attempts": 4, "mode": "standard"},
                        connect_timeout=10,
                        read_timeout=60,
                    ),
                )
    return _client


def _safe_piece(value):
    text = str(value or "").strip().replace("\\", "-").replace("/", "-")
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-.")
    return text or "arquivo"


def make_key(category, original_name=None, *parts):
    pieces = [_safe_piece(category)]
    pieces.extend(_safe_piece(p) for p in parts if p not in (None, ""))
    if original_name:
        stem = _safe_piece(Path(original_name).stem)[:80]
        suffix = Path(original_name).suffix.lower()[:12]
        digest = hashlib.sha256(os.urandom(24)).hexdigest()[:16]
        pieces.append(f"{stem}-{digest}{suffix}")
    return "/".join(pieces)


def guess_content_type(filename, fallback="application/octet-stream"):
    return mimetypes.guess_type(filename or "")[0] or fallback


def upload_fileobj(fileobj, key, content_type=None, metadata=None):
    client = get_client()
    extra = {}
    if content_type:
        extra["ContentType"] = content_type
    if metadata:
        extra["Metadata"] = {str(k): str(v) for k, v in metadata.items() if v is not None}
    try:
        fileobj.seek(0)
    except Exception:
        pass
    client.upload_fileobj(fileobj, bucket_name(), key, ExtraArgs=extra or None)
    return key


def upload_bytes(data, key, content_type="application/octet-stream", metadata=None):
    client = get_client()
    kwargs = {
        "Bucket": bucket_name(),
        "Key": key,
        "Body": data,
        "ContentType": content_type,
    }
    if metadata:
        kwargs["Metadata"] = {str(k): str(v) for k, v in metadata.items() if v is not None}
    client.put_object(**kwargs)
    return key


def upload_path(path, key=None, content_type=None, metadata=None):
    path = Path(path)
    key = key or make_key("migrados", path.name)
    with path.open("rb") as fh:
        return upload_fileobj(fh, key, content_type or guess_content_type(path.name), metadata)


def decode_data_url(data_url):
    if not data_url or "," not in data_url:
        raise ValueError("Data URL inválida.")
    header, payload = data_url.split(",", 1)
    match = re.fullmatch(r"data:([^;]+);base64", header.strip(), flags=re.I)
    if not match:
        raise ValueError("Data URL precisa estar em base64.")
    mime = match.group(1).lower()
    return base64.b64decode(payload, validate=True), mime


def extension_for_mime(mime):
    return {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
        "application/pdf": ".pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    }.get((mime or "").lower(), mimetypes.guess_extension(mime or "") or "")


def presigned_url(key, expires=None, download_name=None, inline=True):
    if not key:
        return None
    # Documentos acadêmicos, contratos, fotos e assinaturas são privados por padrão.
    # URL pública só é usada se houver opt-in explícito no ambiente.
    public_base = _env("R2_PUBLIC_BASE_URL")
    allow_public = str(os.getenv("R2_ALLOW_PUBLIC_URLS", "0")).strip().lower() in {"1", "true", "yes", "sim"}
    if public_base and allow_public:
        return f"{public_base.rstrip('/')}/{quote(key, safe='/')}"
    params = {"Bucket": bucket_name(), "Key": key}
    if download_name:
        disposition = "inline" if inline else "attachment"
        safe_name = str(download_name).replace('"', "")
        params["ResponseContentDisposition"] = f'{disposition}; filename="{safe_name}"'
    return get_client().generate_presigned_url(
        "get_object",
        Params=params,
        ExpiresIn=int(expires or os.getenv("R2_SIGNED_URL_SECONDS", "900")),
    )


def delete_object(key):
    if not key:
        return
    get_client().delete_object(Bucket=bucket_name(), Key=key)


def object_exists(key):
    if not key:
        return False
    try:
        get_client().head_object(Bucket=bucket_name(), Key=key)
        return True
    except Exception:
        return False
