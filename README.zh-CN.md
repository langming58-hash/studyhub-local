# StudyHub Local

[![Release](https://img.shields.io/github/v/release/langming58-hash/studyhub-local?label=release)](https://github.com/langming58-hash/studyhub-local/releases/tag/v0.2.0)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![CI](https://github.com/langming58-hash/studyhub-local/actions/workflows/ci.yml/badge.svg)](https://github.com/langming58-hash/studyhub-local/actions/workflows/ci.yml)

[English](README.md)

StudyHub Local 是一个早期阶段、本地优先的大学课程资料工作区，适用于任何大学。它可以按“课程 -> 周次”整理资料，提供本地预览和搜索、笔记与学习记录，并可选使用 AI，根据你已建立索引的资料回答并显示来源。

原始文件始终保留在你自己控制的文件夹中。生产版本第一次打开时是干净的空工作区，不会捆绑示例课程、老师资料、测试数据库、提取文本或 API 凭证。

## v0.2.0

- 干净首启：新建课程或导入课程文件夹
- English / 简体中文界面，可跟随系统或在设置中切换
- Home、Courses、Search、Study、AI、Settings 六个主要工作区
- 本地 AI 对话历史、来源引用、个人笔记与老师原题保护
- Tauri macOS 桌面原型，后端已打包，终端用户无需安装 Python
- 严格隔离生产资源与测试夹具

本版本不会公开未签名、未公证的 `.app` 或 DMG。必须完成签名、公证和干净 Mac 分发验证后，桌面安装包才会作为正式公开下载提供。

## 截图

![英文干净首启](docs/assets/screenshots/first-run-en.png)

![简体中文干净首启](docs/assets/screenshots/first-run-zh-CN.png)

截图只显示空的生产工作区，不包含课程资料或个人信息。

## 主要功能

- 按课程、周次/模块、资料类型和练习类型整理本地文件
- 预览 PDF、文本、代码、图片及支持的 Office 文件
- 搜索文件名和已提取的可读正文
- 在当前题目、文件、周次或课程范围内询问 AI
- 从引用跳回来源文件，并在可靠时显示页码或幻灯片编号
- 本地保存笔记、收藏、对话、练习记录和错题记录
- 只检索老师提供的题目，不生成新的练习题
- 不配置 OpenAI 也可使用整理、预览、搜索、笔记和学习记录

## 环境要求

- Git、Node.js/npm 和 Python 3
- 建议安装 Poppler（`pdftotext`、`pdfinfo`），用于提取 PDF 正文
- 可选安装 LibreOffice，用于更高保真的 PowerPoint / Word 预览

## 快速开始

```bash
git clone https://github.com/langming58-hash/studyhub-local.git
cd studyhub-local
npm install
python3 -m pip install -r requirements.txt
npm run dev
```

打开终端输出的本机地址，通常是 `http://127.0.0.1:8765`。第一次打开后，新建课程或导入已有课程文件夹。StudyHub 不会自动扫描无关文件夹。

macOS 上的 `Start StudyHub Local.command` 只是源码安装的便捷启动器，不是已签名的桌面正式版。

## 学习资料库

真实学习资料必须放在仓库外。推荐结构示例：

```text
~/StudyLibrary/
├── CS101 - Programming Fundamentals/
│   ├── Week 01/
│   │   ├── 01 Course Materials/Lecture/
│   │   └── 02 Exercises/Tutorial/
│   └── Week 02/
└── ECON201 - Macroeconomics/
```

在本机提取依赖可用时，常见支持格式包括 PDF、DOCX、PPTX、TXT、CSV、Python、R 和 IPYNB。原文件始终是权威来源，不会被转换成与课程无关的格式。

## 可选 OpenAI

OpenAI 集成只在服务端运行，而且完全可选。只在你自己的电脑上创建 `.env.local`：

```text
OPENAI_API_KEY=
OPENAI_MODEL=gpt-5.4
```

不要把 key 放入前端代码、截图、日志、Issue 或 Git 提交。AI 默认使用最窄的合理范围：当前题目、当前文件、当前周次、当前课程。如果索引资料不足以支持回答，StudyHub 会返回“没有来源”，不会编造课程内容。Vector Search 只是可选检索层，本地原始文件始终是唯一权威来源。

## 隐私与安全

- 默认只监听 loopback 本机地址，默认无遥测
- 校验 Host、精确 same-origin、CSRF、请求大小与文件根目录边界
- 运行数据库、提取文本、预览缓存、日志、密钥和学习资料不会进入 Git
- MCP 仅提供本地只读边界
- 正常 CI 会运行隐私与安全验收套件

更改信任边界前，请阅读 [SECURITY.md](SECURITY.md)、[PRIVACY.md](PRIVACY.md) 和 [架构说明](docs/ARCHITECTURE.md)。

## 开发

合成测试夹具只允许放在 `tests/fixtures/` 下，并由测试临时注入，不能进入生产资源或桌面 app bundle。

```bash
npm run ci
npm run desktop:check
```

桌面原型构建与打包验收：

```bash
npm run desktop:setup
npm run desktop:build
npm run desktop:test:packaged
```

另见 [开发说明](docs/DEVELOPMENT.md)、[桌面架构](docs/DESKTOP_ARCHITECTURE.md) 和 [贡献指南](CONTRIBUTING.md)。

## License

[MIT](LICENSE)
