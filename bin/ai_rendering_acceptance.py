#!/usr/bin/env python3
"""AI Markdown/math rendering checks. Uses synthetic text only."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path


NODE_TEST = r"""
const renderer = require(process.cwd() + "/static/ai-renderer.js");
const fs = require("fs");

function assert(name, condition) {
  console.log(`${name}: ${condition ? "PASS" : "FAIL"}`);
  if (!condition) process.exitCode = 1;
}

function visibleText(html) {
  return html
    .replace(/<annotation\b[\s\S]*?<\/annotation>/g, "")
    .replace(/<[^>]+>/g, "")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&amp;/g, "&");
}

function withoutCode(html) {
  return html
    .replace(/<pre\b[\s\S]*?<\/pre>/g, "")
    .replace(/<code\b[\s\S]*?<\/code>/g, "");
}

function hasKatex(html) {
  return html.includes("class=\"katex\"");
}

const screenshotLike = String.raw`这句话怎么翻成微分方程？ “rate of change” 就是 \frac{dN}{dt}

“is proportional to N” 就是

\[
\frac{dN}{dt}=kN
\]

但因为这是放射性衰变，所以通常会写成

$$
\frac{dN}{dt}=-kN \qquad (k>0)
$$

为什么它是可分离变量？ 因为可以写作 $\int \frac{1}{N}\,dN=-k\int dt$。

最终形式是 $N(t)=N_0e^{-kt}$，并且 C^{14} 会衰减。`;

const screenshotHtml = renderer.renderMarkdown(screenshotLike);
const screenshotVisible = visibleText(screenshotHtml);
assert("inline_math", hasKatex(renderer.renderMarkdown(String.raw`Use $x^2$ here.`)));
assert("display_math", hasKatex(renderer.renderMarkdown(String.raw`$$\frac{dN}{dt}=-kN$$`)));
assert("legacy_paren_math", hasKatex(renderer.renderMarkdown(String.raw`Use \(x^2\) here.`)));
assert("legacy_bracket_math", hasKatex(renderer.renderMarkdown(String.raw`\[\frac{dN}{dt}=-kN\]`)));
assert("legacy_naked_frac_recovery", hasKatex(renderer.renderMarkdown(String.raw`rate is \frac{dN}{dt}`)));
assert("chinese_math_mixed", hasKatex(screenshotHtml) && screenshotVisible.includes("这句话"));
assert("mathml_accessibility_output", screenshotHtml.includes("<math "));
assert("subscript_superscript", hasKatex(renderer.renderMarkdown(String.raw`Use N_0 and e^{-kt} and C^{14}.`)));
assert("derivative_integral", hasKatex(renderer.renderMarkdown(String.raw`$\frac{dN}{dt}$ and $\int \frac{1}{N}\,dN$`)));
assert("matrix", hasKatex(renderer.renderMarkdown(String.raw`$$\begin{bmatrix}1 & 2 \\ 3 & 4\end{bmatrix}$$`)));
assert("list_math", hasKatex(renderer.renderMarkdown(String.raw`1. Write $\frac{dN}{dt}=-kN$.
2. Separate variables.`)));
assert("table_math", hasKatex(renderer.renderMarkdown(String.raw`| Quantity | Formula |
| --- | --- |
| Decay | $N=N_0e^{-kt}$ |`)));

const currencyHtml = renderer.renderMarkdown("The price rises from $10 to $12.");
assert("currency_dollar_signs", !hasKatex(currencyHtml) && visibleText(currencyHtml).includes("$10 to $12"));

const codeBlockHtml = renderer.renderMarkdown("```python\nlatex = \"\\\\frac{x}{y}\"\n```");
assert("code_block_exclusion", codeBlockHtml.includes("<pre><code") && !hasKatex(withoutCode(codeBlockHtml)) && visibleText(codeBlockHtml).includes("\\frac{x}{y}"));

const inlineCodeHtml = renderer.renderMarkdown("Keep `\\\\frac{x}{y}` as code.");
assert("inline_code_exclusion", inlineCodeHtml.includes("<code>\\\\frac{x}{y}</code>") && !hasKatex(withoutCode(inlineCodeHtml)));

const filenameHtml = renderer.renderMarkdown("Open calculus_tutorial_3.pdf from folder\\subfolder.");
assert("underscore_filename_safe", !hasKatex(filenameHtml) && visibleText(filenameHtml).includes("calculus_tutorial_3.pdf"));

const malformedHtml = renderer.renderMarkdown(String.raw`Bad formula: $\frac{x}{$ then keep reading.`);
assert("malformed_math_fallback", malformedHtml.length > 0 && visibleText(malformedHtml).includes("then keep reading"));

const xssHtml = renderer.renderMarkdown(String.raw`<script>alert(1)</script> [bad](javascript:alert(1)) <img src=x onerror=alert(1)>`);
assert("xss_html_disabled", !/<script\b/i.test(xssHtml) && !/<img\b/i.test(xssHtml) && !/href=["']javascript:/i.test(xssHtml));

assert("raw_frac_not_visible", !screenshotVisible.includes("\\frac"));
assert("raw_qquad_not_visible", !screenshotVisible.includes("\\qquad"));
assert("raw_bracket_delimiters_not_visible", !screenshotVisible.includes("\\[") && !screenshotVisible.includes("\\]"));
assert("existing_history_raw_source_renders", hasKatex(renderer.renderMarkdown(screenshotLike)));
assert("long_equation_container", renderer.renderMarkdown(String.raw`$$N(t)=N_0e^{-kt}+N_0e^{-2kt}+N_0e^{-3kt}+N_0e^{-4kt}+N_0e^{-5kt}$$`).includes("math-block"));

const server = fs.readFileSync("server.py", "utf8");
assert("openai_math_contract", server.includes("$...$ for inline math") && server.includes("$$...$$ for display math"));
"""


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    with tempfile.NamedTemporaryFile("w", suffix=".cjs", delete=False) as handle:
        handle.write(NODE_TEST)
        script = Path(handle.name)
    try:
        result = subprocess.run(["node", str(script)], cwd=root, text=True, capture_output=True)
        print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, end="")
        return result.returncode
    finally:
        script.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
