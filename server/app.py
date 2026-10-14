"""Authenticated proxy in front of a local manga-image-translator."""

import hashlib
import hmac
import json
import os
import tempfile
from contextlib import asynccontextmanager
from io import BytesIO
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from PIL import Image

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "mit_config.json"
MAX_BYTES = 20 * 1024 * 1024
MAX_DIM = 10_000
MIT_TIMEOUT = 300.0
ALLOWED_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def load_settings() -> tuple[str, str, Path]:
    token = os.environ.get("MANGAFLOW_TOKEN", "").strip()
    if not token:
        raise RuntimeError("MANGAFLOW_TOKEN is not set")
    mit_url = os.environ.get("MIT_URL", "http://127.0.0.1:8000").strip().rstrip("/")
    cache = os.environ.get("MANGAFLOW_CACHE_DIR", "").strip()
    return token, mit_url, Path(cache) if cache else ROOT / "cache"


def make_mit_client() -> httpx.AsyncClient:
    # Ignore HTTP_PROXY so the localhost hop to mit is not sent off-box.
    return httpx.AsyncClient(timeout=MIT_TIMEOUT, trust_env=False)


@asynccontextmanager
async def lifespan(app: FastAPI):
    token, mit_url, cache_dir = load_settings()
    cache_dir.mkdir(parents=True, exist_ok=True)
    for stale in cache_dir.glob(".part-*"):
        stale.unlink(missing_ok=True)
    app.state.token = token
    app.state.mit_url = mit_url
    app.state.cache_dir = cache_dir
    app.state.client = make_mit_client()
    try:
        yield
    finally:
        await app.state.client.aclose()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


def authorized(header: str | None, token: str) -> bool:
    got = ""
    if header:
        scheme, sep, rest = header.partition(" ")
        if sep and scheme.lower() == "bearer":
            got = rest
    return hmac.compare_digest(got.encode("utf-8", "surrogateescape"), token.encode())


def mit_config_json(target_lang: str | None) -> str:
    with CONFIG_PATH.open(encoding="utf-8") as handle:
        cfg = json.load(handle)
    if target_lang:
        cfg.setdefault("translator", {})["target_lang"] = target_lang
    return json.dumps(cfg, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def cache_key(image: bytes, config_json: str) -> str:
    image_hash = hashlib.sha256(image).hexdigest()
    config_hash = hashlib.sha256(config_json.encode()).hexdigest()
    return image_hash + config_hash


def save_cache(path: Path, data: bytes) -> None:
    # Temp file sits next to the destination so os.replace stays on one filesystem.
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmpname = tempfile.mkstemp(dir=path.parent, prefix=".part-")
    tmp = Path(tmpname)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp, path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def image_error(data: bytes) -> str | None:
    try:
        with Image.open(BytesIO(data)) as im:
            width, height = im.size
            if width < 1 or height < 1 or width > MAX_DIM or height > MAX_DIM:
                return "image dimensions exceed 10000px"
            im.load()
    except Exception:
        return "image could not be decoded"
    return None


async def read_body(request: Request) -> bytes | None:
    buf = bytearray()
    async for chunk in request.stream():
        if len(buf) + len(chunk) > MAX_BYTES:
            return None
        buf += chunk
    return bytes(buf)


def error_response(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


@app.get("/health")
async def health(request: Request) -> dict:
    state = request.app.state
    try:
        await state.client.get(state.mit_url + "/", timeout=3.0)
    except httpx.HTTPError:
        return {"ok": True, "mit": False}
    return {"ok": True, "mit": True}


@app.post("/translate")
async def translate(request: Request, target_lang: str | None = None):
    state = request.app.state
    if not authorized(request.headers.get("authorization"), state.token):
        return error_response(401, "unauthorized")
    media = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media not in ALLOWED_TYPES:
        return error_response(415, "unsupported media type")
    raw = await read_body(request)
    if raw is None:
        return error_response(413, "image exceeds 20MB")
    problem = image_error(raw)
    if problem:
        return error_response(400, problem)
    config_json = mit_config_json(target_lang or None)
    path = state.cache_dir / f"{cache_key(raw, config_json)}.png"
    if path.is_file():
        return Response(content=path.read_bytes(), media_type="image/png")
    try:
        upstream = await state.client.post(
            f"{state.mit_url}/translate/with-form/image",
            data={"config": config_json},
            files={"image": ("page", raw, media)},
        )
    except httpx.HTTPError:
        return error_response(502, "mit unreachable")
    if upstream.status_code < 200 or upstream.status_code >= 300:
        return error_response(502, f"mit status {upstream.status_code}")
    png = upstream.content
    if not png.startswith(PNG_MAGIC):
        return error_response(502, "mit did not return a png")
    save_cache(path, png)
    return Response(content=png, media_type="image/png")
