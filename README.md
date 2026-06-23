# SenseVoice ASR Server

基于 [SenseVoiceSmall-GGUF](https://huggingface.co/FunAudioLLM/SenseVoiceSmall-GGUF) 的语音转文字 HTTP 服务。

使用 FunASR llama.cpp 运行时（无需 Python 推理依赖），支持 WAV/MP3/OGG/FLAC/WebM 等格式。

## 项目结构

```
FunAudioLLM/
├── asr_server.py           # FastAPI 服务器主程序
├── requirements.txt        # Python 依赖（仅 Web 框架）
├── run.sh                  # 后台启动脚本
├── README.md
├── bin/                    # FunASR llama.cpp 预编译二进制
│   └── llama-funasr-sensevoice
├── SenseVoiceSmall-GGUF/   # ASR 模型（已下载）
│   └── sensevoice-small-q8.gguf
├── models/                 # 辅助模型
│   └── fsmn-vad.gguf       # VAD 模型
├── audio/                  # 测试音频
│   └── test_silence.wav
└── logs/                   # 服务器日志
```

## 前置依赖

- Python 3.9+
- ffmpeg（用于音频格式转换）
  ```bash
  brew install ffmpeg
  ```

## 快速开始

### 1. 创建虚拟环境并安装依赖

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### 2. 启动服务器

**前台运行：**
```bash
.venv/bin/python asr_server.py --port 9000
```

**后台运行（推荐）：**
```bash
chmod +x run.sh
./run.sh start
```

### 3. 管理后台服务

```bash
./run.sh start     # 启动
./run.sh stop      # 停止
./run.sh restart   # 重启
./run.sh status    # 查看状态
```

## API 使用

服务器启动后访问 http://localhost:9000/docs 查看交互式 API 文档。

### 端点一览

| 端点 | 方法 | 说明 |
|------|------|------|
| `/asr` | POST | 原生接口（支持文件上传 + URL） |
| `/v1/audio/transcriptions` | POST | OpenAI Whisper 兼容接口（可直接替代 Whisper） |
| `/health` | GET | 健康检查 |

### 原生接口 `/asr`

**上传文件转录：**
```bash
curl -X POST http://localhost:9000/asr \
  -F "file=@your_audio.wav"
```

**通过 URL 转录：**
```bash
curl -X POST "http://localhost:9000/asr?audio_url=https://example.com/audio.wav"
```

**保留标签（语言/情感/事件）：**
```bash
curl -X POST "http://localhost:9000/asr?keep_tags=true" \
  -F "file=@your_audio.wav"
```

**响应格式：**
```json
{
  "text": "转录文本内容",
  "duration": 1.23,
  "processing_time": 0.85,
  "language": "zh",
  "error": null
}
```

### OpenAI Whisper 兼容接口 `/v1/audio/transcriptions`

与 OpenAI Whisper API 完全兼容，可直接替代 `http://localhost:8080/v1/audio/transcriptions`。

```bash
curl -X POST http://localhost:9000/v1/audio/transcriptions \
  -F "file=@your_audio.wav" \
  -F "model=sensevoice-small"
```

支持的参数（`model`、`language`、`prompt`、`temperature` 为兼容性保留，自动忽略）：

| 参数 | 类型 | 说明 |
|------|------|------|
| `file` | file | 音频文件（必填） |
| `model` | string | 模型名（忽略，仅兼容） |
| `response_format` | string | `json`（默认）或 `text` |

**使用方式：** 将调用 Whisper 的 URL 从 `http://localhost:8080` 改为 `http://localhost:9000` 即可，无需修改客户端代码。

### 健康检查

```bash
curl http://localhost:9000/health
```

## 支持的音频格式

WAV、MP3、OGG、FLAC、WebM、M4A 等（ffmpeg 支持的格式均可自动转换）。

## 技术细节

- **ASR 引擎**: FunASR llama.cpp 运行时（静态二进制，无 .so 依赖）
- **模型**: SenseVoiceSmall q8 量化（242MB）
- **VAD**: FSMN-VAD（1.6MB）
- **Web 框架**: FastAPI + Uvicorn
- **端口**: 默认 9000（可通过 `--port` 修改）
