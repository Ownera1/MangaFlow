import asyncio
from io import BytesIO
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

import app as appmod
from bench import natural_key

TOKEN = "test-token-xyz"
LIMIT = 20 * 1024 * 1024


def page(fmt="PNG", size=(8, 8), color=(1, 2, 3)) -> bytes:
    image = Image.new("RGB", size, color)
    buf = BytesIO()
    image.save(buf, format=fmt)
    return buf.getvalue()


TRANSLATED = page(color=(9, 9, 9))


def auth(mime="image/png", token=TOKEN):
    headers = {"Content-Type": mime}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return headers


class Upstream:
    def __init__(self):
        self.calls = []
        self.handler = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.handler is not None:
            return self.handler(request)
        if request.method == "GET":
            return httpx.Response(200, content=b"ok")
        self.calls.append(request)
        return httpx.Response(200, content=TRANSLATED)


@pytest.fixture
def upstream():
    return Upstream()


@pytest.fixture
def client(tmp_path, monkeypatch, upstream):
    monkeypatch.setenv("MANGAFLOW_TOKEN", TOKEN)
    monkeypatch.setenv("MANGAFLOW_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("MIT_URL", "http://mit.local")
    monkeypatch.setattr(
        appmod,
        "make_mit_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(upstream)),
    )
    with TestClient(appmod.app) as test_client:
        yield test_client


def test_lifespan_refuses_without_token(monkeypatch):
    monkeypatch.delenv("MANGAFLOW_TOKEN", raising=False)

    async def start():
        async with appmod.lifespan(appmod.app):
            pass

    with pytest.raises(RuntimeError, match="MANGAFLOW_TOKEN"):
        asyncio.run(start())
    monkeypatch.setenv("MANGAFLOW_TOKEN", "   ")
    with pytest.raises(RuntimeError, match="MANGAFLOW_TOKEN"):
        appmod.load_settings()


def test_settings_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("MANGAFLOW_TOKEN", "t")
    monkeypatch.setenv("MIT_URL", "http://mit.example:9/")
    monkeypatch.setenv("MANGAFLOW_CACHE_DIR", str(tmp_path))
    token, url, cache = appmod.load_settings()
    assert token == "t"
    assert url == "http://mit.example:9"
    assert cache == tmp_path


def test_missing_and_wrong_token_are_401(client, upstream):
    source = page()
    missing = client.post("/translate", content=source, headers=auth(token=None))
    wrong = client.post("/translate", content=source, headers=auth(token="nope"))
    other = client.post(
        "/translate",
        content=source,
        headers={"Authorization": f"Token {TOKEN}", "Content-Type": "image/png"},
    )
    assert missing.status_code == wrong.status_code == other.status_code == 401
    assert missing.json()["error"] == "unauthorized"
    assert TOKEN not in missing.text
    assert upstream.calls == []


def test_non_ascii_authorization_is_401(client, upstream):
    response = client.post(
        "/translate",
        content=page(),
        headers=[
            (b"Authorization", "Bearer caf\u00e9".encode("latin-1")),
            (b"Content-Type", b"image/png"),
        ],
    )
    assert response.status_code == 401
    assert upstream.calls == []


@pytest.mark.parametrize("mime", ["text/plain", "image/gif", "image/jpg"])
def test_unsupported_media_type_is_415(client, upstream, mime):
    response = client.post("/translate", content=page(), headers=auth(mime))
    assert response.status_code == 415
    assert upstream.calls == []


def test_content_type_parameters_are_accepted(client, upstream):
    source = page(fmt="JPEG")
    response = client.post(
        "/translate",
        content=source,
        headers=auth("image/jpeg; charset=binary"),
    )
    assert response.status_code == 200
    assert b"image/jpeg" in upstream.calls[-1].content


def test_missing_content_type_is_415(client, upstream):
    response = client.post(
        "/translate",
        content=page(),
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert response.status_code == 415
    assert upstream.calls == []


def test_body_over_20mb_is_413(client, upstream):
    response = client.post(
        "/translate",
        content=b"\x00" * (LIMIT + 1),
        headers=auth(),
    )
    assert response.status_code == 413
    assert upstream.calls == []


def test_body_at_20mb_is_not_413(client, upstream):
    response = client.post("/translate", content=b"\x00" * LIMIT, headers=auth())
    assert response.status_code == 400
    assert upstream.calls == []


def test_read_body_stops_at_the_limit(monkeypatch):
    monkeypatch.setattr(appmod, "MAX_BYTES", 4)
    pulled = {"n": 0}

    class FakeRequest:
        async def stream(self):
            for chunk in (b"ab", b"cd", b"EF-NOT-KEPT", b"later"):
                pulled["n"] += 1
                yield chunk

    assert asyncio.run(appmod.read_body(FakeRequest())) is None
    assert pulled["n"] == 3


def test_bad_image_is_400(client, upstream):
    garbage = client.post("/translate", content=b"not an image", headers=auth())
    empty = client.post("/translate", content=b"", headers=auth())
    wide = client.post(
        "/translate",
        content=page(size=(10001, 1)),
        headers=auth(),
    )
    assert garbage.status_code == empty.status_code == wide.status_code == 400
    assert "decoded" in garbage.json()["error"]
    assert "10000" in wide.json()["error"]
    assert upstream.calls == []


@pytest.mark.parametrize(
    "fmt,mime",
    [("PNG", "image/png"), ("JPEG", "image/jpeg"), ("WEBP", "image/webp")],
)
def test_translate_returns_png(client, upstream, fmt, mime):
    source = page(fmt=fmt, color=(4, 5, 6))
    response = client.post("/translate", content=source, headers=auth(mime))
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/png")
    assert response.content == TRANSLATED
    sent = upstream.calls[-1]
    assert sent.method == "POST"
    assert sent.url == "http://mit.local/translate/with-form/image"
    assert source in sent.content
    assert b'name="config"' in sent.content
    assert b'name="image"' in sent.content
    assert b'"target_lang":"CHS"' in sent.content
    assert b"custom_openai" in sent.content
    assert b"lama_mpe" in sent.content
    assert TOKEN.encode() not in sent.content


def test_dimensions_at_limit_are_sent(client, upstream):
    source = page(size=(10000, 1))
    response = client.post("/translate", content=source, headers=auth())
    assert response.status_code == 200
    assert source in upstream.calls[0].content


def test_cache_hit_calls_mit_once(client, upstream, tmp_path):
    source = page(color=(3, 3, 3))
    first = client.post("/translate", content=source, headers=auth())
    assert first.status_code == 200
    assert first.content == TRANSLATED

    def boom(request):
        raise AssertionError(f"upstream called for {request.url}")

    upstream.handler = boom
    second = client.post("/translate", content=source, headers=auth())
    assert second.status_code == 200
    assert second.content == TRANSLATED
    assert len(upstream.calls) == 1
    cached = list(tmp_path.glob("*.png"))
    assert len(cached) == 1
    assert len(cached[0].stem) == 128
    assert cached[0].read_bytes() == TRANSLATED
    assert list(tmp_path.glob(".part-*")) == []


def test_different_image_and_target_lang_miss_cache(client, upstream):
    client.post("/translate", content=page(color=(1, 1, 1)), headers=auth())
    client.post("/translate", content=page(color=(2, 2, 2)), headers=auth())
    client.post(
        "/translate",
        params={"target_lang": "ENG"},
        content=page(color=(1, 1, 1)),
        headers=auth(),
    )
    again = client.post(
        "/translate",
        params={"target_lang": "ENG"},
        content=page(color=(1, 1, 1)),
        headers=auth(),
    )
    assert again.status_code == 200
    assert len(upstream.calls) == 3
    assert b'"target_lang":"CHS"' in upstream.calls[0].content
    assert b'"target_lang":"ENG"' in upstream.calls[2].content
    assert b'"target_lang":"CHS"' not in upstream.calls[2].content


def test_mit_500_is_502_and_not_cached(client, upstream, tmp_path):
    def crash(request):
        if request.method == "GET":
            return httpx.Response(200, content=b"ok")
        upstream.calls.append(request)
        return httpx.Response(500, content=b"secret-traceback " + TOKEN.encode())

    upstream.handler = crash
    response = client.post("/translate", content=page(), headers=auth())
    assert response.status_code == 502
    assert response.json()["error"] == "mit status 500"
    assert TOKEN not in response.text
    assert list(tmp_path.glob("*.png")) == []
    client.post("/translate", content=page(), headers=auth())
    assert len(upstream.calls) == 2


def test_mit_connect_error_is_502(client, upstream):
    def down(request):
        raise httpx.ConnectError("connection refused")

    upstream.handler = down
    response = client.post("/translate", content=page(), headers=auth())
    assert response.status_code == 502
    assert response.json()["error"] == "mit unreachable"
    assert TOKEN not in response.text


def test_non_png_success_is_502(client, upstream, tmp_path):
    def html(request):
        if request.method == "GET":
            return httpx.Response(200, content=b"ok")
        upstream.calls.append(request)
        return httpx.Response(200, content=b"<html>nope</html>")

    upstream.handler = html
    response = client.post("/translate", content=page(), headers=auth())
    assert response.status_code == 502
    assert list(tmp_path.glob("*.png")) == []


def test_health_without_auth(client, upstream):
    up = client.get("/health")
    assert up.status_code == 200
    assert up.json() == {"ok": True, "mit": True}

    def down(request):
        raise httpx.ConnectError("connection refused")

    upstream.handler = down
    down_response = client.get("/health")
    assert down_response.json() == {"ok": True, "mit": False}


def test_bench_natural_sort():
    names = ["p10.png", "p2.png", "p1.png", "p1.jpeg"]
    ordered = sorted(names, key=lambda name: natural_key(Path(name)))
    assert ordered == ["p1.jpeg", "p1.png", "p2.png", "p10.png"]
