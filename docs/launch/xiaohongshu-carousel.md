# Xiaohongshu Carousel Plan

Use only synthetic screenshots from `docs/assets/`. Do not use real academic screenshots, real course files, private paths, personal account UI, or API settings.

## Slide 1

Text: 大学课程资料太乱？我给自己做了一个本地 Study Hub

Screenshot asset: none or `docs/assets/home-this-week.svg`

Caption: PDF / slides / Tutorial / Lab / Quiz 越堆越乱，所以做了一个本地优先的小工具。

## Slide 2

Text: 按 Course -> Week 整理

Screenshot asset: `docs/assets/course-week-browsing.svg`

Caption: 每门课下面按 Week 放 Materials 和 Exercises，打开就知道这周有哪些东西。

## Slide 3

Text: Materials / Exercises 分开看

Screenshot asset: `docs/assets/materials-exercises.svg`

Caption: Lecture、Slides、Tutorial、Workshop、Lab、Quiz 都有清楚的位置。

## Slide 4

Text: 本地搜索自己的资料

Screenshot asset: `docs/assets/search.svg`

Caption: 先从本机 indexed material 找，不需要把所有资料搬到云端 dashboard。

## Slide 5

Text: Ask GPT 但要有来源

Screenshot asset: `docs/assets/ask-gpt.svg`

Caption: OpenAI 是可选的；开启后回答要基于 indexed material，并显示 Course / Week / File。

## Slide 6

Text: Practice 不让 AI 乱编题

Screenshot asset: `docs/assets/practice.svg`

Caption: 练习题只能从材料里找；找不到就明确说找不到。

## Slide 7

Text: 错题和复习记录

Screenshot asset: `docs/assets/wrong-questions.svg`

Caption: 适合把复习关注点留在本地 runtime data 里，不进公开仓库。

## Slide 8

Text: 本地优先，开源早期版本

Screenshot asset: `docs/assets/settings-privacy.svg`

Caption: localhost only、默认没有 telemetry、不开 API key 也能跑 Demo Mode。

## Post Caption

大学 PDF / PPT / Tutorial / Lab 越堆越乱，所以我给自己做了一个本地 Study Hub：StudyHub Local。

它可以按 Course -> Week 整理资料，支持本地搜索，也可以可选开启 OpenAI，让 AI 基于自己的材料回答并显示来源。默认 Demo Mode，不需要 API key；课程文件留在本机；服务只监听 localhost；默认没有 telemetry。

Repo: https://github.com/langming58-hash/studyhub-local

Release: https://github.com/langming58-hash/studyhub-local/releases/tag/v0.1.4

这是早期开源项目，欢迎试用、提 Issue、PR 和反馈。

Hashtags: #开源项目 #大学学习 #学习工具 #效率工具 #AI学习 #本地优先 #GitHub
