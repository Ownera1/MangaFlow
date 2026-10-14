# 翻译适配层

iOS 阅读器把原图 `POST` 到这里。本服务做 Bearer 鉴权、体积和图片校验、磁盘缓存，再转发给本机的 [manga-image-translator](https://github.com/zyddnys/manga-image-translator)（下称 mit）。

mit 自己没有鉴权。mit 和适配层都只监听 `127.0.0.1`，手机通过 Tailscale 的 HTTPS 进来。

## Windows 上跑 mit（RTX 3060 6GB）

安装步骤以官方 README 为准：<https://github.com/zyddnys/manga-image-translator#installation>。Windows 上先装 [MSVC 生成工具](https://github.com/zyddnys/manga-image-translator#notes-for-windows-users)，再装和本机 CUDA 对应的 PyTorch（<https://pytorch.org/get-started/locally/>），然后在 mit 仓库里 `pip install -r requirements.txt`。Python 用 3.12 或更新，但要和 PyTorch 的轮子匹配。

在 **mit 仓库根目录** 设置翻译用的 OpenAI 兼容接口并启动（默认 `http://127.0.0.1:8000`）：

```bat
set CUSTOM_OPENAI_API_KEY=你的 key
set CUSTOM_OPENAI_API_BASE=https://api.example.com/v1
set CUSTOM_OPENAI_MODEL=模型名
python server/main.py --use-gpu --start-instance
```

`--start-instance` 会拉起本机翻译进程，不加的话请求会停在队列里。首次运行会把模型下载到 mit 仓库的 `models/`，可能超过适配层的 300 秒超时；先在浏览器打开 `http://127.0.0.1:8000` 翻一张图，再从手机调用。

`server/mit_config.json`（本仓库，不是 mit 仓库）是给 6GB 显存的配置：检测和修复边长 1536、修复模型 `lama_mpe`、不放大。适配层把它放进 mit 的 `config` 字段。上面三个 `CUSTOM_OPENAI_*` 由 mit 自己读取，适配层不碰。

## 启动适配层

安装 [uv](https://docs.astral.sh/uv/)。在本仓库的 `server/` 目录：

```powershell
$env:MANGAFLOW_TOKEN = "一长串随机串"
# 可选：$env:MIT_URL = "http://127.0.0.1:8000"
# 可选：$env:MANGAFLOW_CACHE_DIR = "D:\manga-cache"
uv run uvicorn app:app --host 127.0.0.1 --port 8100
```

`MANGAFLOW_TOKEN` 未设置或只有空白时，进程启动失败。请求头是 `Authorization: Bearer <同一个 token>`。

同一台 Windows 上把适配层暴露给手机：

```bat
tailscale serve --bg --https=443 http://127.0.0.1:8100
```

iOS 使用 Tailscale 给出的 `https://<机器名>`，不需要 ATS 例外。8000 和 8100 保持在本机，不要做端口映射。

换了 `CUSTOM_OPENAI_MODEL`，或升级了 mit 及其模型之后，删掉缓存目录再翻。缓存键只含图片字节和 config JSON，模型名不在键里，换模型会命中旧译文。

## 在 Mac 上用 CPU 跑 mit 做质量验证

按官方 pip/venv 步骤克隆 mit。Mac 上安装 PyTorch 的 CPU 包，然后 `pip install -r requirements.txt`。设置同样的三个 `CUSTOM_OPENAI_*`，在 mit 仓库根目录执行：

```bash
python server/main.py --start-instance
```

这条命令在 CPU 上跑。再到本仓库 `server/`，设好 `MANGAFLOW_TOKEN`，执行 `uv run uvicorn app:app --host 127.0.0.1 --port 8100`。CPU 只适合看大约 10 页的嵌字效果。

## bench.py

准备一个目录，放上大约 10 页 jpg、jpeg、png 或 webp。

```bash
cd server
export MANGAFLOW_TOKEN=...
uv run python bench.py /path/to/pages http://127.0.0.1:8100 /path/to/out
```

脚本按文件名自然排序，逐张 `POST /translate`，把译图写成 `out/<原文件名主干>.png`，并写 `out/results.csv`（`filename`、`status`、`seconds`、`output_bytes`）。质量靠看图。有一张不是 HTTP 200 时，csv 仍会写完，进程以状态码 1 退出。

检查适配层：

```bash
cd server && uv run pytest -q
```
