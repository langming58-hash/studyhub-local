# [分享创造] 做了一个本地优先的开源学习资料管理器 StudyHub Local

我平时课程里的 PDF / PPT / Tutorial / Lab / Quiz 和代码文件越来越多，散在下载目录、网盘和各种文件夹里找起来很麻烦，所以做了一个早期开源项目 StudyHub Local。

它主要是把本地学习资料按 Course -> Week -> Materials / Exercises 整理起来。

它的重点不是做一个云端笔记平台，而是本地优先：

- 课程文件留在自己电脑上
- 默认 Demo Mode，不需要 OpenAI API key
- 默认没有 telemetry
- 服务只监听 localhost，安装后在本机打开 http://127.0.0.1:8765
- 支持本地搜索
- Ask GPT 可选，回答要基于 indexed material，并显示来源
- Practice 题不会凭空生成，只能来自真实/演示材料里的题
- 有只读 MCP endpoint，限制在本地资料库范围内

仓库里只包含 TEST1001 / TEST2001 / TEST3001 这类合成 demo 数据，不包含真实课程资料。

Repo: https://github.com/langming58-hash/studyhub-local

Release: https://github.com/langming58-hash/studyhub-local/releases/tag/v0.1.5

目前还是早期版本，很多地方肯定可以继续打磨。欢迎试用、提 Issue、PR，或者从架构、搜索、资料整理方式、跨平台体验这些角度给点反馈。
