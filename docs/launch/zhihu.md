# 我做了一个本地优先的开源学习资料管理器：StudyHub Local

课程资料越来越碎，是我做 StudyHub Local 的起点。一个学期下来，PDF、slides、tutorial、workshop、lab、quiz、代码文件、复习记录可能散在下载目录、浏览器、笔记软件和聊天记录里。云端笔记工具当然能解决一部分问题，但我更想要一个明确以「本地资料为权威来源」的系统。

StudyHub Local 是一个早期开源项目，核心结构很简单：

- Course -> Week -> Course Materials / Exercises
- 本地文件扫描
- SQLite metadata index
- 本地搜索
- 可选 OpenAI Responses API / vector retrieval
- Ask AI 回答时显示来源
- Practice question safety：不生成新题，只从材料中找题
- 只读 MCP endpoint，方便本地集成

我在隐私和安全上做了比较保守的设计。应用第一次打开是干净的空工作区，本地整理、预览、搜索和学习记录不需要 OpenAI API key；默认没有 telemetry；服务器只监听 localhost；真实课程文件应该放在仓库外；`.env.local`、运行时数据库、提取文本、vector metadata、私有路径都会被排除或扫描拦截。

如果开启 OpenAI/vector indexing，选中的文件内容会发送给 OpenAI 做检索，所以我没有把它宣传成「永远 100% 本地」。更准确地说，它是 local-first，AI 是可选的。

这个项目还很早期，限制也很明确：文档提取质量会受格式和本地工具影响，跨平台启动器还可以更好，搜索排序、onboarding、可访问性和 UI polish 都还有空间。

Repo: https://github.com/langming58-hash/studyhub-local

Release: https://github.com/langming58-hash/studyhub-local/releases/tag/v0.3.0-beta.2

欢迎试用、提 Issue、PR，尤其欢迎对本地优先架构、隐私边界、教育工具体验和文档提取的反馈。

beta.2 包含 PDF Reader V1、来源关联笔记与摘录、手动 FSRS 卡片、Canvas OAuth / 作业 / Today 和本地备份导出。仅 Apple Silicon、macOS 13+，ad-hoc 签名、未公证；Canvas 需要学校配置 OAuth 客户端。完整的真实 GUI 备份保存、恢复、撤销验收仍未完成，不包含 PR #44 PDF Highlights。这是预发布测试版。可复用[通用中文文案](general-cn.md)，截图来源见[真实截图记录](../assets/screenshots/README.md)。
