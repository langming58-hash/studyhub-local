(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.StudyHubNotesUI = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  function noteAnchorPayload(file, currentPage, pageCount) {
    const ext = String((file && file.extension) || "").toLowerCase().replace(/^\./, "");
    const mime = String((file && file.mime_type) || "").toLowerCase();
    const isPdf = ext === "pdf" || mime === "application/pdf" || mime.includes("pdf");
    const page = Number(currentPage);
    const pages = Number(pageCount);
    if (
      !isPdf ||
      !Number.isInteger(page) ||
      page < 1 ||
      !Number.isInteger(pages) ||
      pages < 1 ||
      page > pages ||
      page > 10000
    ) {
      return {};
    }
    return { anchorPage: page, pageCount: pages };
  }

  return { noteAnchorPayload: noteAnchorPayload };
});
