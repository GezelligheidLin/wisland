# Wisland · Windows 液态灵动岛

贴在屏幕边缘的液态毛玻璃胶囊，悬停弹性展开。Windows 10 / 11，Python + PySide6。

<p align="center"><img src="docs/home.png" width="720"></p>

## 安装

### 免安装版（推荐）

在右侧 **Releases** 下载最新版本：

- **`wisland-x.y.z.zip`**：解压后双击 `Wisland.exe` 即可运行。配置、缓存和模型都保存在同一个文件夹里，整个文件夹可以随意移动。
- **`Wisland-x.y.z.exe`**：单文件版，双击即可运行（每次启动要先解压，会比目录版慢几秒）。

不需要安装 Python。开机自启：右键灵动岛 → **设置 → 常规 → 开机自启**。

带更新检查的版本会在启动后和常驻期间检查正式发布。打开 **设置 → 版本与更新**，发现可验证签名的新版本时，点击 **立即更新** 即可从 OSS 下载与当前运行方式相符的包。校验和下载完成后，Wisland 会退出、替换程序文件并重启，保留原路径、配置、数据和模型。

早期版本需手动升级一次。可在设置中使用 OSS 的 ZIP 或 EXE 下载入口，或访问 GitHub 发布页。手动更新前退出 Wisland；目录版用新 ZIP 中的 `Wisland.exe` 和 `_internal` 替换原有程序文件，单文件版用新 EXE 替换原文件。保留原文件名可沿用开机自启路径。

### 从源码运行

1. 安装 **Python 3.10 或更新版本**：<https://www.python.org/downloads/windows/>
   （安装时勾选 **Add python.exe to PATH**）
2. 下载本项目源码（或 `git clone https://github.com/GezelligheidLin/wisland.git`），
   路径里最好不要有特殊字符。
3. 双击 **`setup.bat`**：自动创建 `.venv`、补齐 pip、安装依赖（PyPI 连不上时自动改用清华镜像），需要几分钟。
4. 双击 **`wisland.vbs`** 启动。还没运行过 `setup.bat` 时，它会提示并帮你运行。

开机自启：右键灵动岛 → **设置 → 常规 → 开机自启**。

### 维护者配置国内下载源

1. 创建专用于 Wisland 发版的 OSS Bucket，使用标准存储，Bucket 读写权限设为私有。
2. 在该 Bucket 的 **权限控制 → Bucket 授权策略** 中，给所有访问者授予 `wisland/releases/*` 的 `oss:GetObject` 权限。应用这条公开读取规则前，关闭该 Bucket 的「阻止公共访问」。如果账号级「阻止公共访问」开启，仍会拦截公开读取。 [阿里云 Bucket Policy 指南](https://help.aliyun.com/zh/oss/user-guide/oss-bucket-policy/) · [阻止公共访问说明](https://help.aliyun.com/zh/oss/user-guide/block-public-access)
3. 在 GitHub 仓库的 **Settings → Secrets and variables → Actions** 中添加下列 Variables。

   | Variable | 值 |
   | --- | --- |
   | `WISLAND_OSS_BUCKET` | Bucket 名称，例如 `wisland-updates` |
   | `WISLAND_OSS_REGION` | Bucket 地域，例如 `cn-shanghai` |
   | `WISLAND_OSS_DOWNLOAD_BASE` | OSS Bucket 的 HTTPS 根地址，例如 `https://wisland-updates.oss-cn-shanghai.aliyuncs.com` |
   | `WISLAND_OSS_UPLOAD_ORIGIN` | 默认公网 Endpoint 可以上传时留空。使用自定义域名直连 OSS 时，填写同一个 HTTPS 根地址。 |

   下载变量只填 HTTPS 根地址。客户端会自动拼接 `wisland/releases/` 下的对象路径。可以先上传一个测试对象到该目录，用不带签名参数的 HTTPS 对象 URL 确认能公开读取。如果返回 `PublicEndpointForbidden`，请给 Bucket 绑定已备案的自定义域名并配置 HTTPS，再将这个域名同时填入下载和上传变量。 [阿里云默认 Endpoint 限制](https://help.aliyun.com/zh/oss/publicendpointforbidden-error-when-upload-object) · [OSS 绑定自定义域名](https://help.aliyun.com/zh/oss/user-guide/access-buckets-via-custom-domain-names)
4. 将用于上传的 RAM 用户凭据设为 Actions Secrets `OSS_ACCESS_KEY_ID` 和 `OSS_ACCESS_KEY_SECRET`。将权限限制在该 Bucket，授权读取 `wisland/releases/latest.json`、向 `wisland/releases/*` 上传对象，以及失败时终止该前缀下的分片上传。
5. 应用内更新使用 Ed25519 签名清单。在 Actions Variables 中添加 `WISLAND_UPDATE_PUBLIC_KEY`，在 Actions Secrets 中添加对应的 `WISLAND_UPDATE_PRIVATE_KEY`。两者均使用 32 字节原始密钥的 Base64 编码。发版会将公钥嵌入客户端，换钥时需要为旧客户端保留过渡版本。
6. 推送 `vX.Y.Z` tag 后，Actions 先发布 GitHub Release，再把同一份 ZIP 和 EXE 上传到 OSS 的 `wisland/releases/vX.Y.Z/`。两个文件上传完成后才会更新 `wisland/releases/latest.json`。首次发版后，从下载域名确认更新清单和两个文件可以访问。镜像上传失败后可重跑失败的任务。

未设置 `WISLAND_OSS_DOWNLOAD_BASE` 时，工作流只发 GitHub Release，客户端从 GitHub 检查更新。下载域名会随发布包打入客户端，域名变更时应保留原入口，便于已发布的客户端继续检查。

## 功能

- **液态外形**：与屏幕边缘以表面张力相连；按住拖动会拉丝、断开，松手吸附到最近的边缘
- **收起态**：时间天气 / 小形象 / 正在播放，滚轮切换；开始播放音乐时自动显示**滚动歌词**（加宽显示完整一句，右侧可直接播放 / 暂停）；实时字幕时上日语、下中文两行显示；新消息、音量、股票提醒以动画浮现
- **小形象**：发光双眼，跟随鼠标，靠近时随机变换表情；按压会被挤歪
- **音乐**：网易云音乐（直接读取本地播放日志，无需插件）、QQ 音乐、Spotify、B 站 / 浏览器等；歌词来自网易云公开歌词（含翻译）
- **主页**：播放控制、展开方式、可穿透、静音、实时字幕
- **消息**：QQ / 微信 / 钉钉 / 邮件提醒。QQ、微信、钉钉在 Windows 通知提供正文时显示发送者和消息摘要（最多 240 字，仅保留在内存）；仅任务栏闪烁或通知隐藏详情时按可用信息显示提示。邮件只显示“新邮件”。计数为启动后检测到的提醒次数；点击卡片打开应用并清零，钉钉打开路径可在 `notify.open.dingtalk` 中配置。
- **股票**：A 股 / 港股 / 美股行情与分时（腾讯财经公开接口），点 + 搜索添加，右键删除
- **天气**：多城市（右键城市可修改 / 设为默认 / 删除），UAPIs 天气数据、逐小时与 5 天预报、空气质量 / 污染物、分钟级降水提示和 18 项生活指数；每个城市每天自动查询一次并本地缓存，查询失败的可在天气页点刷新重试（成功的不重复请求）；天气图标使用 QWeather Icons
- **控制**：音量、亮度、WLAN、蓝牙（不读取 Wi-Fi 名称，因此不会触发 Windows 的位置访问）；检测到 Clash Verge 时显示代理控制
- **剪贴板**：最近 10 条文字 / 图片，点击复制
- **翻译**：在翻译页的「文本」标签直接选择原文和译文语言，默认自动识别原文并译成简体中文。实时字幕支持日语识别和中文字幕，可滚轮上翻查看历史、点词查词、加入单词本并同步到 Anki。
- **主题**：自动 / 深色 / 浅色 / 胭脂撞色 / 晴空渐变。自动主题根据灵动岛本体当前覆盖的桌面画面判断明暗。
- **主题过渡**：自动明暗切换和手动换主题时，面板配色以约 260 毫秒平滑渐变，连续切换从当前颜色接续。自动模式下背景与配色由同一次采样触发，以相同时长和曲线同步过渡；通过明暗阈值滞回避免中间亮度反复切换。渐变中收到新截图时，从当前混合画面沿剩余缓动曲线继续，同一主题内不反复重启或推迟完成。
- **远程模式**：被 ToDesk / 向日葵等远程控制时自动暂停毛玻璃实时刷新，远端画面里的灵动岛不再闪烁；可在 **设置 → 外观 → 远程模式** 改为始终开启或关闭。

操作：默认悬停展开（停在上次看的页面），鼠标离开收起；可在 **设置 → 常规 → 展开方式** 或主页快捷按钮切换「悬停展开 / 点击展开 / 常驻展开」：点击展开时悬停不再唤出，单击胶囊才展开；**双击**灵动岛切换「长关」（不再自动展开）；右键菜单提供设置、面板展开 / 收起、贴边隐藏 / 恢复和退出。

设置窗口：点击展开面板右上角的齿轮，或从灵动岛右键菜单、托盘右键菜单选择 **设置**。左侧可搜索设置或切换页面，侧栏底部可打开 **版本与更新**。常用偏好修改后立即生效并自动保存。**标签页**至少保留一项，隐藏页面不会关闭对应后台服务。

收起视图默认每 30 秒在时间天气和小形象之间切换，可在 **设置 → 外观 → 自动轮换收起视图** 开关。关闭后仍可用滚轮手动切换。

消息已查看同步：对应应用在前台停留约 1 秒后，清除该应用在 Wisland 中的计数和提醒记录（按应用判断，不代表各会话实际已读）。点击消息卡片或记录可打开对应应用，成功后清除其提醒；没有会话定位信息时仅打开应用，不定位具体聊天。“全部已读”同时清除计数和记录。

贴边隐藏：按 **Ctrl+Alt+H**，从胶囊或面板平滑收缩为当前边缘的细线（上下为横线，左右为竖线）。再次按键立即恢复胶囊；也可在线上连续悬停 **3 秒**唤出，中途移开会重置计时。唤出后移开鼠标，再进入才恢复原有悬停展开行为；展开方式和可穿透设置保持原样。右键菜单提供“贴边隐藏 / 恢复灵动岛”；在 **设置 → 快捷键** 中可修改或禁用该快捷键，配置项为 `hotkeys.edge_hide`。

隐藏胶囊的悬停反馈：进入时轻微变饱满并泛起柔光，同色系亮度渐变从中心向两端扩散；起步和退出时柔和淡入淡出，不出现独立的小圆点。满 3 秒后平滑恢复灵动岛。

待办事项：在展开面板切到 **待办** 标签，即可查看最多四条待办；优先显示待处理提醒、逾期和今天到期的事项。勾选即可完成，短时间内可以撤销。点击事项标题编辑，点击 **＋ 添加** 快速录入，点击 **查看全部** 打开独立管理窗口。托盘也可打开管理窗口，**设置 → 快捷键** 可配置快速录入键。管理窗口支持修改备注、截止和提醒，从「已完成」恢复事项，新建时可选常用预设或自定义提醒日期和时间；对已到点的提醒，可选择「稍后提醒」重新安排，或点「知道了」消除提醒。

每日提醒：新建时选择「每天定时」，也可以在编辑待办时切换提醒方式。按电脑本地时间每天提醒一次；今天已过设定时间时从明天开始。点击「知道了」或「稍后提醒」只处理本轮，不会改变下一次固定提醒。完成事项后停止提醒，恢复事项时重新安排；休眠或退出跨过多天时只补最近一轮。

待办仅保存在当前 Windows 用户的本地应用数据目录，不需要账号。退出或休眠期间不保证准点提示，启动或唤醒后会汇总补查；红点只表示有到点但未处理的提醒。**设置 → 待办** 可关闭弹出提示，清单和红点仍会更新。

## 可选设置

配置文件是 `config.json`（首次启动自动生成）。在 **设置 → 高级** 中可打开、重新加载配置，也可打开数据目录。

| 功能 | 需要什么 |
| --- | --- |
| 翻译、实时字幕的中文翻译 | 在 **设置 → 管理 → 高级 → 翻译** 中填写 API Key，可同时设置服务地址和模型 |
| 实时字幕的音频与显示 | 在 **设置 → 管理 → 高级 → 实时字幕** 中选择音频来源，设置识别草稿与中文字幕 |
| 实时字幕的日语识别 | 无需设置：第一次点「开始聆听」会自动下载识别模型（约 630 MB，可断点续传，连不上 Hugging Face 时自动改用 hf-mirror 镜像） |
| 点词查词的词典 | `.venv\Scripts\python.exe scripts\download_models.py --dict` |
| 天气 | UAPIs 天气接口；可在设置 → 高级 → 天气服务填写 `weather.uapi_key`，留空使用免费额度；每个城市每天自动查询一次并本地缓存，失败的可在天气页点刷新重试 |
| 自选股 | 股票页点 **+** 搜索添加，或编辑 `stocks.watchlist` |
| 邮件未读提醒（不开 Thunderbird） | `mail.imap` 里填 IMAP 授权码 |
| 同步到 Anki | 安装 Anki 插件 AnkiConnect |

## 常见问题

- **双击 vbs 报错 80070002 / 没反应 / No module named 'PySide6'**：依赖没装进 `.venv`，运行 `setup.bat`（不要用全局的 `pip install`）；还不行就删掉 `.venv` 文件夹再运行一次。日志在 `data\wisland.log`。
- **实时字幕没反应**：第一次使用要先下载模型，按钮旁会显示下载进度；下载失败时检查网络 / 代理后再点一次，会接着下载。没有填 DeepSeek Key 时只显示日语、不翻译。
- **setup.bat 提示找不到 Python**：装 Python 时没勾选 Add to PATH；重新运行安装程序选 Modify 勾上即可。
- **没显示正在播放 / 没有歌词**：网易云无需设置（读取它的本地日志）；QQ 音乐需在 设置 → 常规设置 中勾选「显示系统媒体传输控制(SMTC)」。没有接入系统媒体控件的播放器，播放按钮会改发键盘媒体键。周杰伦等网易云没有版权的歌曲没有歌词。
- **不想自动切到音乐 / 不要歌词**：`config.json → media.auto_show` / `media.lyrics` 改为 false。
- **蓝牙磁贴是灰色**：电脑没有蓝牙适配器或驱动不支持系统开关，点击会打开系统蓝牙设置。
- **热键无效**：可能被其他软件占用，可在 **设置 → 快捷键** 中更换。
- **用 ToDesk 等远程控制时灵动岛一直闪**：毛玻璃每次取背景都要把自己临时排除出截图约 17 毫秒（静止时每秒约 8 次）。本机屏幕不受影响，但远控软件持续复制桌面，就会周期性拍到“没有灵动岛”的画面。**设置 → 外观 → 远程模式** 默认自动检测：RDP 会话，或常见远控软件在运行且出现过注入式鼠标输入（本地重新动物理鼠标后自动退出）。检测期间不再取背景，玻璃保留最后一张纹理并跟着灵动岛移动 / 展开，自动明暗切换也暂停。名单外的远控软件或驱动级注入检测不到时，可设为「始终开启」；关闭「参与截图」则让灵动岛完全不进入截图 / 录屏。配置项为 `island.remote_mode`（auto / on / off）。

## 效果图

<p align="center">
<img src="docs/compact_clock.png" height="56">&nbsp;
<img src="docs/compact_music.png" height="56">&nbsp;
<img src="docs/compact_pet.png" height="56">
</p>

<p align="center"><img src="docs/expressions.png" width="860"></p>

<p align="center"><img src="docs/subtitles.png" width="600"></p>

<p align="center">
<img src="docs/theme_light.png" width="280">
<img src="docs/theme_rouge.png" width="280">
<img src="docs/theme_sky.png" width="280">
</p>

---

实时字幕与单词本流水线移植自 [YishengTranslator-White](https://github.com/kayson-39/YishengTranslator-White)（MIT）。MIT License.
