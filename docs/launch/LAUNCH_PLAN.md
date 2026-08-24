# StudyHub Local Public Launch Checklist

Repository: https://github.com/langming58-hash/studyhub-local

Release: https://github.com/langming58-hash/studyhub-local/releases/tag/v0.1.5

This file is a reusable public launch checklist. It must not become a ledger of
the maintainer's personal social accounts, account status, or publication
history.

## Privacy Rule

Do not record personal account URLs, private messages, login state, account
eligibility, account age, cookies, platform verification details, or personal
screenshots in this repository.

Social publishing status belongs outside the code repository unless the linkage
is intentionally part of a public professional profile.

## Public Posting Principles

- Link to the GitHub repository or a release, not a localhost URL.
- Mention localhost only as an install instruction, for example: after
  installation, open `http://127.0.0.1:8765` on your own machine.
- Use only synthetic demo screenshots or GIFs from `docs/assets/`.
- Do not show real course files, teacher questions, private filenames, browser
  tabs, API settings, local paths, or personal account UI.
- Keep the tone practical: early-stage, open source, local-first, privacy-aware,
  and feedback-seeking.
- Do not ask for upvotes, stars, follows, or engagement.
- Do not claim broad adoption, enterprise readiness, or security maturity beyond
  what is documented and tested.

## Platforms To Consider

| Platform | Draft | Suggested angle | Notes |
|----------|-------|-----------------|-------|
| LinkedIn | `linkedin.md` | Engineering/open-source project note | Keep it personal and professional. |
| X | `x.md` | Short release note or small thread | Keep the repository URL visible in text. |
| Xiaohongshu | `xiaohongshu.md`, `xiaohongshu-carousel.md` | Student workflow and visual demo | Use synthetic carousel media only. |
| Zhihu | `zhihu.md` | Longer product/engineering write-up | Avoid private course examples. |
| Hacker News | `show-hn.md`, `show-hn-first-comment.md` | Show HN if appropriate | Follow the site's normal posting flow and rules. |
| V2EX | `v2ex.md` | Personal project share | Follow the node's current rules. |
| Reddit r/opensource | `reddit-opensource.md` | Open-source project discussion | Follow subreddit rules and flair requirements. |
| Reddit r/selfhosted | `reddit-selfhosted.md` | Only if there is a clear self-hosting fit | Do not force a post if the fit is weak. |

## Pre-Post Checklist

- Run `npm run ci`.
- Re-run the privacy scanner before publishing new copy or media.
- Check every image/GIF manually for synthetic-only content.
- Confirm the repository URL is correct.
- Confirm no direct personal social post URL is being added to this repo.
- Confirm no exact publication timestamp is being added to this repo.
- Confirm no account eligibility, login, activation, age, karma, or verification
  status is being added to this repo.
- Save only generic drafts, templates, and reusable launch notes here.

## Response Handling

Use `INSTALL_SUPPORT.md` for install help and `replies/` for reusable response
templates.

When recording feedback, summarize themes without copying private messages,
personal identifiers, screenshots, or account-specific context.
