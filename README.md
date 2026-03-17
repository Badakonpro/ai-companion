# AI Companion

> 一款 Galgame 风格的桌面 AI 伴侣与互动剧本应用。  
> 她有自己的性格、情绪和记忆；你是男主角，用选择塑造剧情走向。

<p align="center">
  <img src="assets/icon.png" width="128" alt="AI Companion icon"/>
</p>

<p align="center">
  <a href="https://github.com/Badakonpro/ai-companion/releases/latest">
    <img src="https://img.shields.io/github/v/release/Badakonpro/ai-companion?style=flat-square&label=下载" alt="Release"/>
  </a>
  <img src="https://img.shields.io/badge/版本-0.6.0--beta-ff6b81?style=flat-square" alt="Version"/>
  <img src="https://img.shields.io/badge/platform-macOS%20%7C%20Windows-blue?style=flat-square" alt="Platform"/>
  <img src="https://img.shields.io/badge/electron-35.7.5-47848f?style=flat-square" alt="Electron"/>
  <img src="https://img.shields.io/badge/python-3.9%2B-3776ab?style=flat-square" alt="Python"/>
</p>

---

## 特性

### 剧本系统（v0.6 全面升级）
- **多维度剧本生成** — 从题材、尺度、初始关系（12 种可选）、女主性格等多维约束出发，AI 生成包含完整世界观、人设、路线地图的深度剧本种子
- **两阶段蓝图系统** — 选择种子后进入蓝图预览：角色详情、男主方向、6-8 个关键剧情节点、4 条路线地图（纯爱 / 支配 / 决裂 / 救赎等），确认后才进入故事
- **男主画像积累** — 玩家每次选择都实时分析大胆度、温柔度、支配欲、诚实度、理性度五个维度，画像注入系统提示让男主行为持续合理化
- **路线化选项** — 选项附带路线提示标签；AI 根据故事进度（开局 / 发展 / 高潮 / 收束四阶段）生成具有战略意义的分叉选择

### 核心引擎
- **动态情绪** — 情绪引擎实时追踪好感、紧张、信赖、舒适四维状态，随对话内容和关系进展变化
- **长期记忆** — 向量检索（SQLite-vss）+ 自动压缩摘要，跨会话持久化记忆
- **叙事弧线** — 多段长周期故事弧管理，支持篇章完结与新篇开启
- **一致性守卫** — 自动校验人物设定不矛盾，防止 AI 遗忘已建立的事实
- **运行时模型切换** — 应用内直接切换 LLM（7 款预设 + 自定义），无需重启

### 通用
- **完全本地运行** — LLM 推理通过 [Ollama](https://ollama.com) 在本地执行，数据不离开设备
- **跨平台桌面** — 打包为 macOS `.dmg` 和 Windows `.exe`，开箱即用
- **存档与回溯** — 随时创建存档点，支持回溯到任意历史节点

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
ollama pull sorc/qwen3.5-instruct-heretic   # 默认对话 LLM
ollama pull nomic-embed-text                # 向量嵌入模型
```

> 也可在应用内切换到其他 Ollama 模型，如 `qwen2.5:14b`、`llama3.1:8b` 等。

### 下载安装包（推荐）

前往 [Releases](https://github.com/Badakonpro/ai-companion/releases/latest) 页面：

- **macOS (Apple Silicon)**：下载 `AI.Companion-0.6.0-beta-arm64.dmg`
- **Windows**：下载 `AI.Companion-0.6.0-beta-win.exe`

> macOS 用户注意：应用未经 Apple 公证，首次打开请**右键 → 打开**，在弹窗中点击「打开」即可绕过 Gatekeeper。

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
├── electron/                   # Electron 主进程 & preload
│   ├── main.js
│   └── preload.js
├── frontend/                   # React 19 + TypeScript + Vite
│   └── src/
│       ├── App.tsx             # 主界面（大厅 / 蓝图预览 / 故事三阶段）
│       ├── index.css           # 全局样式
│       └── lib/
│           └── config.ts       # API 端点常量
├── backend/                    # Python FastAPI 后端
│   ├── main.py                 # 路由、请求模型、启动入口
│   ├── persona.py              # 角色人格配置（林夕）
│   ├── logger.py               # 结构化日志
│   ├── requirements.txt
│   └── core/                   # 核心业务模块
│       ├── story_orchestrator.py    # LLM 调用 & 流程编排（流式 / 非流式）
│       ├── prompt_generator.py      # 动态 System Prompt 构建（男主画像 + 蓝图注入）
│       ├── protagonist_profiler.py  # 男主性格画像分析（五维特征积累）
│       ├── emotion_engine.py        # 情绪状态机（四维情绪）
│       ├── memory_manager.py        # 对话记忆压缩与摘要
│       ├── vector_store.py          # 语义向量检索（SQLite-vss + ONNX）
│       ├── arc_manager.py           # 叙事弧线管理（多段故事线）
│       ├── event_store.py           # SQLite 持久化（会话/状态/画像/蓝图）
│       ├── consistency_guard.py     # 人设一致性校验
│       ├── context_budgeter.py      # Token 预算管理
│       ├── model_manager.py         # 运行时模型切换
│       ├── rate_limiter.py          # 请求限速
│       └── perf_metrics.py          # 性能监控
├── assets/                     # 图标等静态资源
├── devctl.sh                   # 开发运维脚本（start / stop / build / release）
└── package.json                # Electron 项目配置 & 打包配置
```

---

## 构建桌面应用

```bash
# 一键构建前端 + 打包 Electron 安装包
./devctl.sh build

# 或者手动分步：
cd frontend && npm run build          # 1. 构建前端到 backend/static/
cd ..
npx electron-builder --mac            # 2. 打包 macOS DMG（arm64）
npx electron-builder --win            # 2. 打包 Windows EXE
```

产物输出至 `dist/` 目录。

---

## 技术栈

| 层 | 技术 |
|----|------|
| 桌面框架 | Electron 35 + electron-builder 26 |
| 前端 | React 19 + TypeScript + Vite 8 |
| 后端 | Python FastAPI + Uvicorn |
| LLM 推理 | Ollama（本地，支持 Apple Silicon / NVIDIA GPU） |
| 向量检索 | SQLite-vss + ONNX Runtime（nomic-embed-text 768 维） |
| 持久化 | SQLite（APSW 3.42+） |
| 提示词策略 | 男主画像注入 + 剧本蓝图注入 + 四阶段动态选项指令 |
| CI/CD | GitHub Actions → GitHub Releases |

---

## 开发规范

### API 约定

所有接口以 `/api/` 开头，静态资源挂载在最后（`/`）。常用端点：

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/story/tags` | GET | 获取题材标签 + 初始关系类型 |
| `/api/story/seeds/generate` | POST | 多维约束生成剧本种子 |
| `/api/story/blueprint` | POST | 根据种子生成完整剧本蓝图 |
| `/api/story/turn` | POST | 非流式剧情推进 |
| `/api/story/turn/stream` | POST | SSE 流式剧情推进 |
| `/api/sessions` | GET/POST | 会话列表 / 创建会话 |
| `/api/sessions/{id}/blueprint` | PUT | 保存蓝图到会话 |
| `/api/config/model` | GET/PUT | 查询 / 切换当前 LLM |

### 前端阶段状态机

```
lobby  →（选择种子）→  blueprint  →（确认蓝图）→  story
                                  ↑
                         （重新生成蓝图）
   ←─────────────────────（返回大厅）────────────────────
```

### 后端并发模式

Turn 端点（流式/非流式）均采用 `asyncio.gather` 并行读取：
- **pre-gen**：`session_state` + `facts` + `memory_context` + `emotion` + `protagonist_profile` + `blueprint`
- **post-gen**：`episode_extraction` + `emotion_update` + `protagonist_profile_update`（并行写入）

### 数据库表简列

| 表名 | 作用 |
|------|------|
| `sessions` | 会话元信息 |
| `story_turns` | 每轮对话记录 |
| `session_state` | 张力/信任/进度等状态值 |
| `session_facts` | 人物关系事实清单 |
| `episodes` | 摘要化的记忆片段 |
| `arcs` | 叙事弧线（篇章） |
| `snapshots` | 存档点 |
| `protagonist_profile` | 男主五维性格画像 |
| `script_blueprints` | 剧本蓝图（结构化 JSON） |

---

## 配置

关键配置项位于 `backend/config.py`，可直接修改或通过 `.env` 文件覆盖：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `OLLAMA_MODEL` | `sorc/qwen3.5-instruct-heretic` | 默认对话模型名 |
| `OLLAMA_CHAT_URL` | `http://127.0.0.1:11434/api/chat` | Ollama 接口地址 |
| `EMBEDDING_MODEL` | `nomic-embed-text` | 嵌入模型名 |
| `BACKEND_PORT` | `8000` | 后端监听端口 |
| `RATE_LIMIT_PER_MINUTE` | `20` | 每分钟最大请求数 |
| `MAX_INPUT_LENGTH` | `2000` | 单条消息最大字符数 |

---

## 版本历史

| 版本 | 日期 | 主要变更 |
|------|------|---------|
| **0.6.0-beta** | 2026-03-17 | 男主画像系统、两阶段蓝图生成、多维剧本约束、路线化选项、初始关系选择器、运行时模型切换 |
| 0.5.1 | — | 窗口标题修复、模型选择菜单（7 款预设 + 自定义输入）|
| 0.5.0-beta | — | 首个公开测试版：Electron 打包、流式 SSE、存档回溯 |

---

## 发布新版本

```bash
# 修改 package.json / frontend/package.json 中的版本号后：
./devctl.sh release
```

脚本会自动打 Git tag 并推送，触发 GitHub Actions 自动构建 macOS / Windows 安装包并发布到 Releases。

---

## License

MIT © [Badakonpro](https://github.com/Badakonpro)
