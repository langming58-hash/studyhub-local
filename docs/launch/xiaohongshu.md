# 大学资料太乱？本地StudyHub

大学里的 PDF / PPT / Tutorial / Lab / Quiz 真的很容易越堆越乱。

所以我做了一个开源早期版本：StudyHub Local。

它的思路很简单：
Course -> Week -> Materials / Exercises

每周的 lecture、slides、tutorial、lab、quiz 都放在清楚的位置；需要复习时可以先本地搜索自己的资料。

如果可选开启 OpenAI，Ask AI 也会基于自己的资料回答，并尽量给出 Course / Week / File 来源。练习题也不会让 AI 乱编，找不到真实材料就明确说找不到。

几个我很在意的点：

- localhost / local-first
- 课程文件留在自己电脑
- 第一次打开是空工作区，不捆绑示例课程
- OpenAI 可选，不配置 API 也能整理、预览、搜索和做学习记录
- 默认没有 telemetry
- 合成测试资料只留在自动化测试中，不进入生产 App

项目已经开源，还很早期，欢迎试用和反馈。

GitHub 搜：langming58-hash / studyhub-local

#大学学习 #学习工具 #开源项目 #AI学习 #效率工具 #GitHub #本地优先
