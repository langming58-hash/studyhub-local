(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.StudyHubReviewCardsUI = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  function anchorLabel(card) {
    return card && card.anchor_state ? String(card.anchor_state) : "unset";
  }

  function anchorChip(card, t, escapeHtml) {
    if (!card || !card.anchor_page) return "";
    const page = escapeHtml(t("file.notePage", { page: card.anchor_page }));
    if (anchorLabel(card) === "ok") {
      return `<button class="chip chip-button" type="button" data-card-source="${escapeHtml(card.id)}">${page}</button>`;
    }
    return `<span class="chip" title="${escapeHtml(t("file.pageAnchorUnavailable"))}">${page} · ${escapeHtml(t("file.pageAnchorStale"))}</span>`;
  }

  function cardViewHtml(card, reveal, t, escapeHtml) {
    if (!card) return "";
    const id = escapeHtml(card.id);
    return `
      <article class="review-card">
        <div class="chips">${anchorChip(card, t, escapeHtml)}<span class="chip">${escapeHtml(card.filename || "")}</span></div>
        <p class="review-card-front">${escapeHtml(card.front)}</p>
        ${
          reveal
            ? `<p class="review-card-back">${escapeHtml(card.back || "")}</p>`
            : `<button class="button secondary" type="button" data-card-reveal="${id}">${escapeHtml(t("cards.reveal"))}</button>`
        }
      </article>`;
  }

  function gradeBar(card, t, escapeHtml) {
    const id = escapeHtml(card.id);
    return `<div class="review-grades" aria-label="${escapeHtml(t("study.reviewGrade"))}">${["again", "hard", "good", "easy"]
      .map((grade) => `<button class="button secondary compact" type="button" data-card-grade="${id}" data-grade="${grade}">${escapeHtml(t("study.grade." + grade))}</button>`)
      .join("")}</div>`;
  }

  return { anchorLabel: anchorLabel, anchorChip: anchorChip, cardViewHtml: cardViewHtml, gradeBar: gradeBar };
});
