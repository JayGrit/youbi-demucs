# ydbi-demucs

`demucs` 是 YouBi 的音频分离阶段，负责把 downloader 产出的源音频拆成人声
`audio_vocals_url` 和背景/伴奏 `audio_bgm_url`。它只处理
`distributor_task_stages` 中 `stage_name='demucs'`、`sub_stage='main'` 的任务，
不创建下游任务，也不直接释放 whisper；阶段成功后只把输出写入 `task_info` 并把本
阶段置为 success，后续由 `distributor` 路由。

## 技术选型

服务使用 Python 3.12，音频分离依赖 Demucs 子模块。`pyproject.toml` 中包含
`torch`、`torchaudio`、`torchcodec`、`dora-search`、`julius`、`openunmix` 等 Demucs
运行依赖。入口是 `ydbi_demucs/main.py`，命令脚本为 `ydbi-demucs`；本地测试脚本为
`ydbi-demucs-local-test`。对象存储使用 MinIO，数据库使用 `mysql-connector-python`。
工作目录默认是系统临时目录下的 `ydbi/demucs/<task_id>`，不是 `/work/demucs`；这点和
其他 Docker 服务不同，部署时如果需要固定磁盘位置应先改配置。

## 输入输出

`handle()` 从当前阶段行与 `task_info` 合并后的 row 中读取 `audio_source_url`。如果该
字段为空，直接抛 `FileNotFoundError`。输入会下载到当前任务 session 的
`media/audio_source<suffix>`，suffix 来自 URL 路径；没有后缀时使用 `.audio`。分离完成
后输出固定写成 WAV：

- 人声：本地 `media/audio_vocals.wav`，上传到
  `<task_id>/demucs/audio_vocals.wav`，写入 `task_info.audio_vocals_url`。
- 背景：本地 `media/audio_bgm.wav`，上传到
  `<task_id>/demucs/audio_bgm.wav`，写入 `task_info.audio_bgm_url`。

返回 mapping 中保留了旧字段 `audio_vocals_path`、`audio_bgm_path`，但当前值为空；真实
跨阶段交接使用 MinIO URL。finally 中会删除整个 session，避免大音频临时文件残留。

## Demucs 运行细节

核心逻辑在 `ydbi_demucs/demucs.py`。服务不会从 pip 包直接假定 demucs 可用，而是按
`demucs_source_candidates()` 查找源码：优先使用 `DEMUCS_REPO`，其次是本仓库
`submodule/demucs`，再尝试历史 `YouDub-webui/submodule/demucs`。如果都找不到，会提示
执行 `git submodule update --init --recursive` 或配置 `DEMUCS_REPO`。

运行前先通过 `ffprobe` 读取音频时长；失败时再尝试 `torchaudio.info()`。时长用于选择
runtime：

- 普通音频：模型 `htdemucs_ft`，`shifts=1`，`segment=None`，`jobs=0`。
- 长音频：超过 `DEMUCS_LONG_AUDIO_SECONDS=1800` 秒时启用，模型切换为 `htdemucs`，
  `shifts=0`，segment 为 10 秒，按 `DEMUCS_LONG_AUDIO_CHUNK_SECONDS=600` 秒切块。

设备选择由 `device_candidates()` 决定。`DEVICE=auto` 时优先 CUDA，其次 Apple MPS，
最后 CPU；如果显式配置 `mps` 会 fallback 到 CPU，显式 `cuda*` 也会 fallback 到 CPU。
每次设备失败都会释放 Python GC、CUDA cache 或 MPS cache，然后尝试下一个设备；CPU
失败则终止。

普通音频路径调用 `Separator.separate_audio_file()`，取 `separated["vocals"]` 作为人声，
其他 stem 相加作为背景。长音频路径先用 ffmpeg 把源音频按 chunk 切成 44.1kHz 双声道
WAV，对每个 chunk 单独分离，保存 `vocals_<index>.wav` 和 `bgm_<index>.wav`，最后用
ffmpeg concat 拼成完整 `audio_vocals.wav` 和 `audio_bgm.wav`。这样可以避免超长音频
一次性进入 Demucs 造成显存或内存压力。`_clamp_segment_to_model()` 会读取模型允许的
最大 segment，如果配置超过模型限制，会降到模型上限并记录 warning。

## 数据表与状态流转

服务使用统一阶段表 `distributor_task_stages`。`worker.run_polling_worker()` 每轮先写
`service_heartbeat`，再回收超过 2 小时的 running demucs 阶段为 ready，然后查找第一条
`stage_name='demucs' AND status='ready'` 的行。领取时写 running、`started_at` 和
`operator=DEVICE`；执行成功后 `db.mark_success()` 先把输出字段 upsert 到 `task_info`，
再把 `distributor_task_stages` 中当前 task 的 `demucs/main` 置 success；失败时写 failed
和错误信息。

相关表：

- `distributor_task_stages`：阶段状态。
- `task_info`：读 `audio_source_url`，写 `audio_vocals_url/audio_bgm_url`。
- `service_heartbeat`：记录 demucs 心跳，只有设备列在
  `Macbook Air M4/Macmini M2/LPXB/MY_HP/LPXB_HP/TXY` 之一时写入。
- `downloader_submission`、`uploader_account`、`uploader_task` 在 db.py 中有兼容函数
  或空实现引用，但当前 demucs 主流程不推进提交和上传账号计数。

## 与路由的关系

是否运行 demucs 由 distributor 根据 `task_info.has_background_audio` 决定。
`has_background_audio=0` 的 subtitle/dubbing 类任务会从有效 DAG 中移除 demucs，
下游 whisper 直接依赖最近有效父节点。demucs 自身不判断任务类型，也不主动跳过；只要
数据库释放了 ready 行，它就要求 `audio_source_url` 存在并执行分离。

## 业务边界与部署注意

demucs 的业务边界非常窄：它只把一个音频输入拆成两条音频输出，不读取字幕、不读取
翻译结果、不生成 speaker 分段，也不关心最终上传平台。这样做的好处是显存/模型依赖被
限制在单一服务内，其他阶段即使运行在轻量机器上也不需要安装 torch 和 Demucs 模型。
如果线上出现“后续 whisper 没有人声”的问题，应先检查 `task_info.audio_vocals_url`
是否存在、MinIO 对象是否可下载，再看 demucs 日志中的模型、设备和长音频分块信息。
如果 `audio_source_url` 本身为空，问题通常在 downloader 或 distributor 路由，而不是
demucs。

部署时还要注意 CPU fallback 不是性能优化，而是最后兜底。长音频在 CPU 上分离可能非常
慢，worker 的 running 超时是 2 小时，超时后会被回收到 ready 并可能再次尝试。因此生产
上应尽量让 `DEVICE=auto` 能识别到 CUDA 或 MPS，并确认 ffmpeg/ffprobe 在 PATH 中。
长音频分块虽然降低峰值显存，但会生成大量临时 WAV；如果系统临时目录空间不足，应该把
`WORK_ROOT` 改到容量更大的磁盘。服务每个任务结束后会删除 session，但进程被强杀时仍
可能留下临时目录，需要结合 backupper 或人工巡检清理。

## 测试关注点

当前测试重点覆盖失败任务完成和 worker 状态回收。修改 demucs 时应特别关注三类风险：
第一，输出字段名必须保持 `audio_vocals_url/audio_bgm_url`，否则 whisper、speaker 和
combiner 都会找不到输入；第二，不能把本地路径作为跨阶段输出写入数据库，旧字段
`audio_vocals_path/audio_bgm_path` 现在刻意返回空字符串；第三，长音频分块必须保持
chunk 顺序和采样率一致，否则拼接后的人声/背景会产生错位或点击声。涉及模型参数、
设备选择、临时目录或上传路径的修改，至少应跑单元测试和一次本地短音频分离验证。

## 本地运行

```bash
cd /Users/hoshuuch/Money/YouBi/services/demucs
pip install -e .
git submodule update --init --recursive
ydbi-demucs
```

常用配置在 `config.py`：

- `DEMUCS_MODEL`、`DEMUCS_SHIFTS`、`DEMUCS_SEGMENT`、`DEMUCS_JOBS`。
- `DEMUCS_LONG_AUDIO_SECONDS`、`DEMUCS_LONG_AUDIO_MODEL`、
  `DEMUCS_LONG_AUDIO_SEGMENT`、`DEMUCS_LONG_AUDIO_CHUNK_SECONDS`。
- `DEVICE`：`auto/cuda/mps/cpu` 等。
- `DEMUCS_REPO`：外部 Demucs 源码路径。
- MySQL/MinIO 连接常量。

检查命令：

```bash
pytest
python -m compileall ydbi_demucs
```
