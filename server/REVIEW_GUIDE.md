# 审查定位

改动在 `server/`。根目录 `.gitignore` 只追加 `server/cache/` 和 `server/.venv/`。

## 文件

- `app.py`：进程入口。只有 `POST /translate` 和 `GET /health`。
- `mit_config.json`：转发给 mit 的配置。6GB 显存：`custom_openai`、检测/修复 1536、`lama_mpe`、不放大。
- `bench.py`：按文件名自然排序，把目录里的图逐张 POST 出去，写译图和 `results.csv`。
- `tests/test_app.py`：把 `make_mit_client` 换成 `httpx.MockTransport`，不发真实网络请求。
- `pyproject.toml`：uv。运行依赖 fastapi、uvicorn、httpx、pillow；开发依赖 pytest。
- `README.md`：Windows 上装和启动 mit、启动适配层、Tailscale HTTPS、Mac CPU 抽查、bench。

## app.py

- `load_settings` 26–32：读 `MANGAFLOW_TOKEN`（空白则抛错）、`MIT_URL`（默认 `http://127.0.0.1:8000`）、`MANGAFLOW_CACHE_DIR`（默认 `server/cache`）。
- `make_mit_client` 35–37：httpx 客户端，超时 300s，`trust_env=False`。
- `lifespan` 40–53：启动时调用 `load_settings`，失败则进程起不来；创建缓存目录，删残留 `.part-*`，退出时关闭客户端。
- `authorized` 59–65：取出 `Bearer` 凭证，与 token 做 `hmac.compare_digest`。
- `mit_config_json` 68–73：读 JSON，用 query `target_lang` 覆盖后，按键排序并压缩。
- `cache_key` 76–79：`sha256(图片字节) + sha256(上面那段 JSON 的 UTF-8)`，文件名 `<key>.png`。
- `save_cache` 82–93：缓存目录里的临时文件，写完 `os.replace`。
- `image_error` 96–105：Pillow 真正解码；宽或高不在 1…10000 则拒绝。
- `read_body` 108–114：按块累计，超过 20MB 立即停，不把超限的那一块放进缓冲区。
- `error_response` 117–118：正文 `{"error": "..."}`。
- `health` 121–128：3 秒内 GET mit 的 `/`。收到 HTTP 响应则 `mit` 为 true。
- `translate` 131–163：整条翻译调用链。

## 调用链

启动：`lifespan` → `load_settings` → `make_mit_client`。

`POST /translate`：`authorized`（否则 401）→ Content-Type 白名单（415）→ `read_body`（413）→ `image_error`（400）→ `mit_config_json` → `cache_key`。缓存文件存在则直接回 PNG。否则 `POST {MIT_URL}/translate/with-form/image`，表单字段 `image` 和 `config`。连不上为 502；状态不是 2xx 为 502；2xx 但没有 PNG 文件头为 502，并且不写缓存。通过则 `save_cache`，响应 `image/png`。

`GET /health` 不鉴权。OpenAPI 文档路由已关掉。

## 取舍和限制

- 缓存键不含 `CUSTOM_OPENAI_API_KEY` / `API_BASE` / `MODEL`。换模型要手动删缓存，否则会返回旧图。
- 同一键同时未命中时可能请求 mit 两次。`os.replace` 保证读到的是完整文件。Windows 上如果该文件正被另一个进程读着，替换可能失败，这次请求会 500。
- 不限制 mit 回包大小，也不排队。翻译超时 300 秒；mit 第一次下载模型时可能先 502，重试即可。
- 适配层访问 mit 时不走 `HTTP_PROXY`。mit 自己访问大模型时仍然使用它的环境变量。
- 502 只带回短句，不转发 mit 正文。代码路径不打印 token。
- `compare_digest` 在长度不同时直接返回 false，比较时间可能暴露长度，不暴露内容。

## 测试覆盖了什么

401（无 token、错 token、错 scheme）、415、大于 20MB 的 413、正好 20MB 不因此 413、`read_body` 越限后不再读下一块、坏图和超宽 400、边长 10000 可以通过、jpeg/png/webp 返回 PNG 且上游正文含配置和原图、带 charset 的 `image/jpeg`、同图第二次不调用 mit、换图或换 `target_lang` 会调用、mit 500 / 连接失败 / 200 但不是 PNG 都是 502 且不写缓存、`/health` 在 mit 可达和不可达时都无需鉴权、未设置 token 时 `lifespan` 失败。bench 只测了自然排序。

未覆盖：真实 mit、bench 的 HTTP 往返、并发写同一缓存键、卡满 300 秒、Tailscale、Windows 上的 `os.replace`。Starlette 提示 TestClient 搭配 httpx 已弃用；依赖名单里不能加 httpx2，所以这条警告在 pytest 配置里滤掉了。
