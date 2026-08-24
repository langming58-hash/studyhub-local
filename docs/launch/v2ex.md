# [分享创造] 做了一个本地优先的开源学习资料管理器 StudyHub Local

我做了一个早期开源项目 StudyHub Local，用来把大学课程里的 PDF、slides、tutorial、lab、quiz、代码文件按 Course -> Week -> Materials / Exercises 整理起来。

它的重点不是做一个云端笔记平台，而是本地优先：

- 课程文件留在自己电脑上
- 默认 Demo Mode，不需要 OpenAI API key
- 默认没有 telemetry
- 服务只监听 localhost
- 支持本地搜索
- Ask GPT 可选，回答要基于 indexed material，并显示来源
- Practice 题不会凭空生成，只能来自真实/演示材料里的题
- 有只读 MCP endpoint，限制在本地资料库范围内

仓库里只包含 TEST1001 / TEST2001 / TEST3001 这类合成 demo 数据，不包含真实课程资料。

Repo: https://github.com/langming58-hash/studyhub-local

Release: https://github.com/langming58-hash/studyhub-local/releases/tag/v0.1.4

欢迎试用、提 Issue、PR 或给一点架构/安全/跨平台体验方面的反馈。
