# AI Companion

> 一款 Galgame 风格的桌面 AI 伴侣应用。她有自己的性格、情绪和记忆，不像聊天机器人，更像一个真实的人。

<p align="center">
  <img src="assets/icon.png" width="128" alt="AI Companion icon"/>
</p>

<p align="center">
  <a href="https://github.com/Badakonpro/ai-companion/releases/latest">
    <img src="https://img.shields.io/github/v/release/Badakonpro/ai-companion?style=flat-square&label=下载" alt="Release"/>
  </a>
  <img src="https://img.shields.io/badge/platform-macOS%20%7C%20Windows-blue?style=flat-square" alt="Platform"/>
  <img src="https://img.shields.io/badge/electron-35.7.5-47848f?style=flat-square" alt="Electron"/>
  <img src="https://img.shields.io/badge/python-3.9%2B-3776ab?style=flat-square" alt="Python"/>
</p>

---

## 特性

- **真实人格** — 角色林夕，24 岁全栈工程师，有鲜明的脾气和观点，绝不会暴露自己是 AI
- **动态情绪** — 情绪引擎追踪情绪状态，响应方式随关系进展和对话内容实时变化
- **长期记忆** — 向量检索（SQLite-vss）+ 自动压缩摘要，跨会话持久记住发生的一切
- **叙事弧线** — 故事推进系统，维护多段长周期故事线
- **完全本地运行** — LLM 推理通过 [Ollama](https://ollama.com) 在本地执行，数据不离开你的设备
- **跨平台桌面** — 打包为 macOS `.dmg` 和 Windows `.exe`，开箱即用

---

## 截图

> *(截图待补充)*

---

## 快速开始

### 前置依赖

| 依赖 | 版本要求 |
|------|---------|
| [Ollama](https://ollama.com) | 最新版 |
| Python | 3.9+ |
| Node.js | 18+ |

拉取所需模型：

```bash
ollama pull sorc/qwen3.5-instruct-heretic   # 对话 LLM
ollama pull nomic-embed-text                # 向量嵌入模型
```

### 下载安装包（推荐）

前往 [Releases](https://github.com/Badakonpro/ai-companion/releases/latest) 页面：

- **macOS (Apple Silicon)**：下载 `AI Companion-x.x.x-arm64.dmg`
- **Windows**：下载 `AI Companion-x.x.x-win.exe`

> macOS 用户注意：应用未经 Apple 公证，首次打开请**右键 → 打开**，在弹窗中点击"打开"即可绕过 Gatekeeper。

### 本地开发运行

```bash
git clone https://github.com/Badakonpro/ai-companion.git
cd ai-companion

# 安装 Electron 依赖
npm install

# 启动后端（自动创建 venv 并安装依赖）
./devctl.sh start_backend

# 另开终端，启动前端开发服务器
./devctl.sh start_frontend

# 或者直接用 Electron 窗口打开（同时启动后端）
npm run electron:dev
```

后端默认监听 `http://localhost:8000`，前端开发服务器监听 `http://localhost:5173`。

---

## 项目结构

```
ai-companion/
├── electron/           # Electron 主进程 & preload
│   ├── main.js
│   └── preload.js
├── frontend/           # React + TypeScript + Vite
│   └── src/
├── backend/            # Python FastAPI 后端
│   ├── main.py
│   ├── persona.py      # 角色人格配置
│   ├── config.py       # 全局配置（端口、模型名等）
│   ├── requirements.txt
│   └── core/           # 核心业务模块
│       ├── story_orchestrator.py   # LLM 调用 & 流程编排
│       ├── emotion_engine.py       # 情绪状态机
│       ├── memory_manager.py       # 记忆压缩与摘要
│       ├── vector_store.py         # 语义向量检索
│       ├── arc_manager.py          # 叙事弧线管理
│       ├── context_budgeter.py     # Token 预算管理
│       ├── rate_limiter.py         # 请求限速
│       └── perf_metrics.py         # 性能监控
├── assets/             # 图标等静态资源
├── devctl.sh           # 开发运维脚本
└── package.json        # Electron 项目配置 & 打包配置
```

---

## 构建桌面应用

```bash
# 构建前端并打包为 Electron 应用，同时生成 DMG/EXE 安装包
./devctl.sh build

# 或者手动构建：
cd frontend && npm run build          # 1. 构建前端到 backend/static/
cd .. && npx electron-builder --mac   # 2. 打包 macOS DMG（arm64）
```

产物输出至 `dist/` 目录。

---

## 技术栈

| 层 | 技术 |
|----|------|
| 桌面框架 | Electron 35 + electron-builder 26 |
| 前端 | React 19 + TypeScript + Vite |
| 后端 | Python FastAPI + Uvicorn |
| LLM 推理 | Ollama（本地，支持 Apple Silicon / NVIDIA GPU） |
| 向量检索 | SQLite-vss + ONNX Runtime（nomic-embed-text 768 维） |
| 持久化 | SQLite（APSW 3.42+） |
| CI/CD | GitHub Actions → GitHub Releases |

---

## 配置

关键配置项位于 `backend/config.py`，可直接修改或通过 `.env` 文件覆盖：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `OLLAMA_MODEL` | `sorc/qwen3.5-instruct-heretic` | 对话模型名 |
| `OLLAMA_CHAT_URL` | `http://127.0.0.1:11434/api/chat` | Ollama 接口地址 |
| `EMBEDDING_MODEL` | `nomic-embed-text` | 嵌入模型名 |
| `BACKEND_PORT` | `8000` | 后端监听端口 |
| `RATE_LIMIT_PER_MINUTE` | `20` | 每分钟最大请求数 |
| `MAX_INPUT_LENGTH` | `2000` | 单条消息最大字符数 |

---

## 发布新版本

```bash
# 修改 package.json / backend/config.py 中的版本号后：
./devctl.sh release
```

脚本会自动打 Git tag 并推送，触发 GitHub Actions 自动构建 macOS / Windows 安装包并发布到 Releases。

---

## License

MIT © [Badakonpro](https://github.com/Badakonpro)
