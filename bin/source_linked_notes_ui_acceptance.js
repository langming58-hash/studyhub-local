#!/usr/bin/env node
"use strict";

const ui = require("../static/notes-ui.js");

let failures = 0;
function check(name, ok) {
  console.log(`${name}: ${ok ? "PASS" : "FAIL"}`);
  if (!ok) failures += 1;
}
function eq(actual, expected) {
  return JSON.stringify(actual) === JSON.stringify(expected);
}

check("pdf_with_dot_extension_anchors", eq(ui.noteAnchorPayload({ extension: ".pdf" }, 2, 5), { anchorPage: 2, pageCount: 5 }));
check("pdf_uppercase_anchors", eq(ui.noteAnchorPayload({ extension: ".PDF" }, 1, 3), { anchorPage: 1, pageCount: 3 }));
check("non_pdf_no_anchor", eq(ui.noteAnchorPayload({ extension: ".docx" }, 2, 5), {}));
check("page_zero_no_anchor", eq(ui.noteAnchorPayload({ extension: ".pdf" }, 0, 5), {}));
check("negative_page_no_anchor", eq(ui.noteAnchorPayload({ extension: ".pdf" }, -1, 5), {}));
check("fractional_page_no_anchor", eq(ui.noteAnchorPayload({ extension: ".pdf" }, 2.5, 5), {}));
check("beyond_page_count_no_anchor", eq(ui.noteAnchorPayload({ extension: ".pdf" }, 6, 5), {}));
check("missing_page_count_no_anchor", eq(ui.noteAnchorPayload({ extension: ".pdf" }, 2, undefined), {}));

if (failures > 0) {
  console.error(`Notes UI acceptance failures: ${failures}`);
  process.exit(1);
}
console.log("Notes UI acceptance: PASS");
