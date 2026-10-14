# iOS 嵌字翻译审查索引

当前页编码成 JPEG，`POST {serverURL}/translate?target_lang=`，用返回的 PNG 替换显示。未改 `server/`、`AppDelegate` 里的 `ImagePipeline`、app 名和 bundle id。没有新依赖，做法跟 `UpscaleProcessor`、`PageInterceptorProcessor` 一样。

## 新增

- `Aidoku/Core/Utilities/TranslationKeychain.swift`：`token()` 11 行，`setToken` 20 行。service 是 `MangaFlow.translation`，空字符串会删除条目。共 29 行。
- `Aidoku/Features/Settings/TranslationTokenView.swift`：`TranslationTokenView` 8 行。SecureField 保存进 Keychain，并 post `Translation.token`（16–18 行）。`TextSetting.secure` 会写 UserDefaults，所以 token 用单独页面。
- `Aidoku/Features/Reader/Processing/TranslateProcessor.swift`
  - `TranslationConfig.current()` 14 行：读 serverURL、targetLang（空则 CHS）和 Keychain token，缺一项就返回 nil。
  - `TranslationClient.upload` 35 行、`endpoint` 56 行：POST、`Content-Type: image/jpeg`、Bearer、超时 300s，只接受 200。
  - `identifier` 71 行：包含 server 和 lang，不含 token。
  - `process(_:)` 78 行返回 nil。入口是 `process(_:context:)` 82 行，经 `processImage` 92 行的 `BlockingThrowingTask` 调 `translate` 96 行。JPEG 质量 0.92，编码 `container.image`。失败 throw。`safeDescription` 139 行写日志，不含 token。
  - `fallbackRequest` 115 行：错误是 `processingFailed` 且 processor 是 `TranslateProcessor`，移除后数量下降才返回去掉翻译的请求。
- `AidokuTests/TranslateProcessorTests.swift`：`process` 31 行在全局队列跑 processor。`successRequest` 53、`serverError` 67、`networkError` 74、`notConfigured` 81、`identifierChanges` 89。stub 是 `TranslationURLProtocol` 118 行。测试注入 config，不读用户 Keychain。

## 修改

- `ImageProcessingSettingsKey.getProcessorSettingsKey` 17–20 行：键加上 translate、lang、server。不含 token。
- `AppDelegate` 118–120 行：默认关闭翻译，server 为空，lang 为 CHS。
- `Settings.swift` 533–575 行：翻译组（开关、服务地址、CHS/CHT/ENG、token 页）。
- `SettingsView.pageContentHandler` 311 行：挂上 `TranslationTokenView`。
- `ReaderSettingsView` 147–157 行：阅读中的 `Reader.translatePages` 开关。
- `ReaderViewController` 338–341 行：上述 key 变化时从当前页重载章节。
- `en.lproj/Localizable.strings` 555–562 行，`zh-Hans.lproj/Localizable.strings` 515–522 行。

## 插入点

顺序都是 PageInterceptor → CropBorders → Translate → Downsample/Upscale，条件是 `Reader.translatePages`。

- 分页 URL：`ReaderPageView.imageRequest` 210 行。
- 分页内存图：`setPage` 154 行，`await translate`，失败留原图。这条路径不写 Nuke 缓存，再次进入会重试。
- 分页 base64：`setPageImage(base64:)` 362 行。失败仍显示原图，386 行跳过 `storeCachedImage`，避免占住译图 key。
- 分页 CBZ：`setPageImage(zipURL:)` 394 行解出文件后走 `setPageImage(url:)`。
- Webtoon URL：`pageImageRequest` 370 行。
- Webtoon base64：`loadImage(base64:)` 488 行，失败同样不缓存（515 行）。
- Webtoon CBZ：`loadImage(zipURL:)` 524 行解出后走 `loadImage(url:)`。
- Webtoon 内存图 `loadPage` 320 行原先不跑 Upscale，这次也没有加处理器。

## 失败回退

- 分页：`setPageImage(url:)` 262 行保存带翻译的 request，`startImageTask` 272 行加载。295 行 `fallbackRequest` 去掉 `TranslateProcessor` 再加载。`currentImageRequest` 仍是译图请求，重进该页会再试翻译。
- Webtoon：`loadImage(url:)` 341 行保存 request，`loadImage(request:)` 405 行同样回退，成功走 `showLoadedImage` 437 行。

## 预翻译（只做分页）

`ReaderPagedViewController` 47 行：`ImagePrefetcher(destination: .memoryCache)`。`pageViewController(_:didFinishAnimating:)` 1007 行先调用 `scheduleTranslationPrefetch` 1479 行，再处理 `pagesToPreload == 0`。窗口是显示序号 +1、+2 的后两页；RTL 只对调 dataSource 的前后，不改变这个方向。`translationRequest` 1509 行用 `ReaderPageView.imageRequest`，URL 和 processors 与页面视图相同。`replaceTranslationPrefetch` 1523 行按 URL 加 processor identifier 停掉窗口外的请求。离开章节或内存警告（115 行）走 `cancelTranslationPrefetch` 1538 行。Webtoon、内存图、base64、文本不预热。

## CBZ 预热：已做

`storeArchiveEntry` 51 行按 archive URL 和 path 记住文件 URL，同一 store 再次解同一条目得到同一 URL。`ReaderViewController` 27 行创建的 store 同时给分页 VM（744 行）和页面视图（323 行）。预热 1513 行和显示 394 行因此命中同一个请求。`.txt` 跳过。

## 限制与未测

- `process(_:context:)` 会占住一个 Nuke processing 槽，最长 300s。取消页面不会取消已经发出的 HTTP。
- `jpegData` 把方向烘进像素。非 `.up` 的图再套原 orientation 会转两次。漫画页一般是 `.up`。
- 预热只覆盖当前章的后两张显示页。双页时窗口可能含已在屏上的那一页，Nuke 会合并请求。
- identifier 不含 token。改 token 会重载并重试失败页；已经成功的译图仍走缓存。
- 开关打开但没配 URL 或 token 时，每页记一条 `notConfigured`，然后显示原图。
- 测试没跑。沙箱里 `xcodebuild build-for-testing` 写不了 DerivedData，也写不了 `~/Library/Caches/org.swift.swiftpm` 的 manifest 缓存。本机没有 iOS 模拟器 runtime。界面是 UIKit，没有做浏览器验证。
