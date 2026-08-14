"""Download, deduplicate, and optimize Notion images for local embedding."""

from __future__ import annotations

import base64
import hashlib
import io
import logging
import mimetypes
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse, urlsplit

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

logger = logging.getLogger(__name__)

MAX_IMAGE_DIMENSION = 1280
MAX_IMAGE_BYTES = 1_500_000
IMAGE_QUALITY = 85
_IMAGE_EXTS = {"png", "jpg", "jpeg", "gif", "webp", "bmp"}


def _hash_url(url: str) -> str:
    """Legacy cache name used for compatibility with URL-keyed files."""
    return hashlib.sha256(url.encode()).hexdigest()[:16]


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def _guess_ext(url: str, content_type: str | None) -> str:
    """Guess file extension from URL or Content-Type header."""
    path = url.split("?")[0]
    ext = Path(path).suffix.lstrip(".").lower()
    if ext in _IMAGE_EXTS or ext == "svg":
        return ext
    if content_type:
        ext = content_type.split("/")[-1].lower()
        if ext == "jpeg":
            return "jpg"
        if ext in _IMAGE_EXTS or ext == "svg+xml":
            return "svg" if ext == "svg+xml" else ext
    return "png"


def _prepare_image(data: bytes, ext: str) -> tuple[bytes, str]:
    """Resize/compress large raster images while preserving small files."""
    if ext == "svg":
        return data, ext

    try:
        from PIL import Image, ImageOps

        image = Image.open(io.BytesIO(data))
        image = ImageOps.exif_transpose(image)
        if len(data) <= MAX_IMAGE_BYTES and max(image.size) <= MAX_IMAGE_DIMENSION:
            return data, ext
        has_alpha = "A" in image.getbands() or "transparency" in image.info
        if image.mode == "P" and "transparency" in image.info:
            image = image.convert("RGBA")
        elif image.mode not in ("RGB", "RGBA"):
            image = image.convert("RGBA" if has_alpha else "RGB")

        dimension = MAX_IMAGE_DIMENSION
        for _ in range(4):
            image.thumbnail((dimension, dimension), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            if has_alpha:
                output_ext = "png"
                image.save(output, format="PNG", optimize=True)
            else:
                output_ext = "jpg"
                image.save(
                    output,
                    format="JPEG",
                    quality=IMAGE_QUALITY,
                    optimize=True,
                    progressive=True,
                )
            if len(output.getvalue()) <= MAX_IMAGE_BYTES:
                return output.getvalue(), output_ext
            dimension = int(dimension * 0.8)
    except Exception:
        logger.warning("Image optimization failed; keeping original bytes", exc_info=True)
    return data, ext


def _write_content_file(
    cache_dir: Path,
    data: bytes,
    ext: str,
) -> Path:
    """Write optimized bytes under a content hash and return the cache path."""
    optimized, optimized_ext = _prepare_image(data, ext)
    digest = _hash_bytes(optimized)
    existing = list(cache_dir.glob(f"{digest}.*"))
    if existing:
        return existing[0]

    path = cache_dir / f"{digest}.{optimized_ext}"
    temp_path = path.with_suffix(f".{optimized_ext}.tmp")
    temp_path.write_bytes(optimized)
    temp_path.replace(path)
    return path


def _legacy_cached_path(cache_dir: Path, image_url: str) -> Path | None:
    """Return an existing URL-keyed cache file from older versions."""
    existing = list(cache_dir.glob(f"{_hash_url(image_url)}.*"))
    return existing[0] if existing else None


def _download_bytes_once(
    image_url: str,
    timeout: float,
) -> tuple[bytes, str | None]:
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        response = client.get(image_url)
        response.raise_for_status()
    return response.content, response.headers.get("content-type")


_download_bytes = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential_jitter(initial=0.5, max=4),
    retry=retry_if_exception_type(httpx.HTTPError),
    reraise=True,
)(_download_bytes_once)


def download_image(
    image_url: str,
    cache_dir: Path,
    timeout: float = 30.0,
) -> str | None:
    """Download, deduplicate, and locally cache one image.

    New files are content-addressed, so identical bytes reachable through
    different Notion pre-signed URLs share one file.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    parsed = urlparse(image_url)

    if parsed.path.startswith("/images/"):
        candidate = cache_dir / Path(parsed.path).name
        if not candidate.exists():
            logger.warning("Local image URL not found in cache: %s", image_url)
            return None
        return str(_write_content_file(cache_dir, candidate.read_bytes(), candidate.suffix.lstrip(".") or "png"))

    if image_url.startswith("data:"):
        try:
            header, b64data = image_url.split(",", 1)
            mime_type = header.split(":")[1].split(";")[0]
            data = base64.b64decode(b64data)
            return str(_write_content_file(cache_dir, data, _guess_ext(image_url, mime_type)))
        except Exception as exc:
            logger.warning("Failed to cache inline image %s: %s", image_url[:60], exc)
            return None

    legacy = _legacy_cached_path(cache_dir, image_url)
    if legacy is not None:
        return str(_write_content_file(cache_dir, legacy.read_bytes(), legacy.suffix.lstrip(".") or "png"))

    try:
        data, content_type = _download_bytes(image_url, timeout)
    except httpx.HTTPError as exc:
        logger.warning("Failed to download image %s: %s", image_url[-60:], exc)
        return None

    return str(_write_content_file(cache_dir, data, _guess_ext(image_url, content_type)))


def cleanup_image_cache(cache_dir: Path, keep_names: set[str]) -> int:
    """Remove unreferenced image files from one page cache directory."""
    if not cache_dir.exists():
        return 0
    removed = 0
    for path in cache_dir.iterdir():
        if not path.is_file() or path.name in keep_names:
            continue
        if path.suffix.lstrip(".").lower() not in _IMAGE_EXTS:
            continue
        try:
            path.unlink()
            removed += 1
        except OSError:
            logger.warning("Failed to remove stale image cache file %s", path)
    return removed


def image_to_base64(local_path: str) -> str | None:
    """Read a local image file and return a base64 data URL."""
    try:
        data = Path(local_path).read_bytes()
    except OSError as exc:
        logger.warning("Failed to read cached image %s: %s", local_path, exc)
        return None
    mime_type = mimetypes.guess_type(local_path)[0] or "image/png"
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{mime_type};base64,{b64}"


def pack_image_url(local_url: str, remote_url: str | None) -> str:
    """Keep a local web URL while embedding the original remote source."""
    if not remote_url or remote_url == local_url:
        return local_url
    return f"{local_url}?remote={quote(remote_url, safe='')}"


def local_image_url(value: str | None) -> str | None:
    """Return the local path from packed/legacy image metadata."""
    if not value:
        return None
    parts = urlsplit(value)
    if "remote" in parse_qs(parts.query):
        return parts._replace(query="", fragment="").geturl()
    return value


def remote_image_url(value: str | None) -> str | None:
    """Return the original remote image URL when available."""
    if not value:
        return None
    remote = parse_qs(urlsplit(value).query).get("remote", [""])[0]
    if remote:
        return unquote(remote)
    return value


def unpack_image_url(value: str) -> tuple[str, str | None]:
    """Return ``(local/display_url, original_remote_url)``."""
    remote = remote_image_url(value)
    local = local_image_url(value)
    if remote == local:
        remote = None
    return local or value, remote
