(function attachStudyHubAIRenderer(root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory(require("katex"));
    return;
  }
  root.StudyHubAIRenderer = factory(root.katex);
})(typeof globalThis !== "undefined" ? globalThis : window, function createRenderer(katexLib) {
  const MATH_COMMANDS = new Set([
    "frac",
    "sqrt",
    "sum",
    "prod",
    "int",
    "iint",
    "iiint",
    "partial",
    "lim",
    "begin",
    "vec",
    "bar",
    "hat",
    "overline",
    "underline",
    "alpha",
    "beta",
    "gamma",
    "delta",
    "epsilon",
    "theta",
    "lambda",
    "mu",
    "pi",
    "rho",
    "sigma",
    "tau",
    "phi",
    "omega",
    "Delta",
    "Sigma",
    "Pi",
    "Omega",
    "infty",
    "to",
    "le",
    "ge",
    "neq",
    "approx",
    "cdot",
    "times",
    "qquad",
    "quad",
    "log",
    "ln",
    "exp",
    "sin",
    "cos",
    "tan",
  ]);

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function isEscaped(text, index) {
    let count = 0;
    for (let i = index - 1; i >= 0 && text[i] === "\\"; i -= 1) count += 1;
    return count % 2 === 1;
  }

  function safeUrl(rawUrl) {
    try {
      const url = new URL(rawUrl, "http://localhost");
      if (!["http:", "https:", "mailto:"].includes(url.protocol)) return "";
      return escapeHtml(rawUrl);
    } catch (_error) {
      return "";
    }
  }

  function renderTextFormatting(text) {
    let html = escapeHtml(text);
    html = html.replace(/\[([^\]\n]{1,220})\]\(([^)\s]+)\)/g, (match, label, url) => {
      const href = safeUrl(url);
      if (!href) return match;
      return `<a href="${href}" target="_blank" rel="noreferrer noopener">${label}</a>`;
    });
    html = html.replace(/\*\*([^*\n][\s\S]*?[^*\n])\*\*/g, "<strong>$1</strong>");
    html = html.replace(/(^|[\s(])\*([^*\n]+)\*/g, "$1<em>$2</em>");
    return html.replace(/\\\$/g, "$");
  }

  function renderMath(tex, displayMode) {
    const source = String(tex || "").trim();
    if (!source) return "";
    if (!katexLib || typeof katexLib.renderToString !== "function") {
      const cls = displayMode ? "math-block math-fallback" : "math-inline math-fallback";
      return `<span class="${cls}">${escapeHtml(source)}</span>`;
    }
    try {
      const rendered = katexLib.renderToString(source, {
        displayMode,
        throwOnError: false,
        strict: "warn",
        trust: false,
        output: "htmlAndMathml",
      });
      return displayMode ? `<div class="math-block">${rendered}</div>` : `<span class="math-inline">${rendered}</span>`;
    } catch (_error) {
      const cls = displayMode ? "math-block math-error" : "math-inline math-error";
      return `<span class="${cls}" title="This formula could not be fully rendered.">${escapeHtml(source)}</span>`;
    }
  }

  function isMathCommandAt(text, index) {
    const match = text.slice(index).match(/^\\([A-Za-z]+)/);
    return Boolean(match && MATH_COMMANDS.has(match[1]));
  }

  function isSafeMathBoundary(text, index) {
    const prev = text[index - 1] || "";
    return !prev || !/[\w./-]/.test(prev);
  }

  function readNakedTexExpression(text, index) {
    if (!isMathCommandAt(text, index) || !isSafeMathBoundary(text, index)) return null;
    let end = index;
    let depth = 0;
    while (end < text.length) {
      const char = text[end];
      if ("，。！？；\n\r".includes(char)) break;
      if (char === "{") {
        depth += 1;
        end += 1;
        continue;
      }
      if (char === "}") {
        depth = Math.max(0, depth - 1);
        end += 1;
        continue;
      }
      if (char === "\\") {
        const command = text.slice(end).match(/^\\[A-Za-z]+/);
        if (command && MATH_COMMANDS.has(command[0].slice(1))) {
          end += command[0].length;
          continue;
        }
        if (depth > 0) {
          end += 1;
          continue;
        }
        break;
      }
      if (/\s/.test(char)) {
        const nextIndex = end + 1 + (text.slice(end + 1).match(/^\s*/) || [""])[0].length;
        const next = text[nextIndex] || "";
        if (next === "\\" || /[()[\]+-=]/.test(next)) {
          end = nextIndex;
          continue;
        }
        break;
      }
      if (depth > 0 || /[A-Za-z0-9_^+\-=*/|<>()\[\],.:]/.test(char)) {
        end += 1;
        continue;
      }
      break;
    }
    if (end <= index) return null;
    return { tex: text.slice(index, end), end };
  }

  function readNakedSymbolExpression(text, index) {
    if (!isSafeMathBoundary(text, index)) return null;
    const patterns = [
      /^[A-Za-z]\([A-Za-z0-9]+\)\s*=\s*[A-Za-z](?:_\{?[A-Za-z0-9]+\}?)(?:e\^\{[^}]+\}|2\^\{[^}]+\})?/,
      /^[A-Za-z](?:_\{?[A-Za-z0-9]+\}?|\^\{[^}]+\}|\^[A-Za-z0-9])(?:e\^\{[^}]+\})?/,
      /^[A-Za-z]\\to\\infty/,
    ];
    const slice = text.slice(index);
    for (const pattern of patterns) {
      const match = slice.match(pattern);
      if (match) {
        const end = index + match[0].length;
        const next = text[end] || "";
        if (!next || !/[\w./-]/.test(next)) return { tex: match[0], end };
      }
    }
    return null;
  }

  function findClosingDollar(text, start) {
    for (let i = start + 1; i < text.length; i += 1) {
      if (text[i] !== "$" || text[i + 1] === "$" || isEscaped(text, i)) continue;
      if (/\s/.test(text[i - 1] || "")) continue;
      return i;
    }
    return -1;
  }

  function canOpenDollarMath(text, index) {
    const next = text[index + 1] || "";
    if (text[index + 1] === "$" || isEscaped(text, index)) return false;
    if (!next || /\s|\d/.test(next)) return false;
    return true;
  }

  function renderInlineMarkdown(text) {
    const pieces = [];
    let buffer = "";
    const flushText = () => {
      if (!buffer) return;
      pieces.push(renderTextFormatting(buffer));
      buffer = "";
    };

    for (let i = 0; i < text.length; ) {
      if (text[i] === "`") {
        const end = text.indexOf("`", i + 1);
        if (end !== -1) {
          flushText();
          pieces.push(`<code>${escapeHtml(text.slice(i + 1, end))}</code>`);
          i = end + 1;
          continue;
        }
      }
      if (text.startsWith("\\(", i)) {
        const end = text.indexOf("\\)", i + 2);
        if (end !== -1) {
          flushText();
          pieces.push(renderMath(text.slice(i + 2, end), false));
          i = end + 2;
          continue;
        }
      }
      if (text.startsWith("\\[", i)) {
        const end = text.indexOf("\\]", i + 2);
        if (end !== -1) {
          flushText();
          pieces.push(renderMath(text.slice(i + 2, end), false));
          i = end + 2;
          continue;
        }
      }
      if (text.startsWith("$$", i) && !isEscaped(text, i)) {
        const end = text.indexOf("$$", i + 2);
        if (end !== -1) {
          flushText();
          pieces.push(renderMath(text.slice(i + 2, end), false));
          i = end + 2;
          continue;
        }
      }
      if (text[i] === "$" && canOpenDollarMath(text, i)) {
        const end = findClosingDollar(text, i);
        if (end !== -1) {
          flushText();
          pieces.push(renderMath(text.slice(i + 1, end), false));
          i = end + 1;
          continue;
        }
      }
      const nakedTex = readNakedTexExpression(text, i);
      if (nakedTex) {
        flushText();
        pieces.push(renderMath(nakedTex.tex, false));
        i = nakedTex.end;
        continue;
      }
      const nakedSymbol = readNakedSymbolExpression(text, i);
      if (nakedSymbol) {
        flushText();
        pieces.push(renderMath(nakedSymbol.tex, false));
        i = nakedSymbol.end;
        continue;
      }
      buffer += text[i];
      i += 1;
    }
    flushText();
    return pieces.join("");
  }

  function isDisplayMathStart(line) {
    const trimmed = line.trim();
    return trimmed.startsWith("$$") || trimmed.startsWith("\\[");
  }

  function collectDisplayMath(lines, index) {
    const trimmed = lines[index].trim();
    const opener = trimmed.startsWith("$$") ? "$$" : "\\[";
    const closer = opener === "$$" ? "$$" : "\\]";
    let first = trimmed.slice(opener.length);
    if (first.endsWith(closer) && first.length > closer.length) {
      return { tex: first.slice(0, -closer.length).trim(), next: index + 1 };
    }
    const content = [];
    if (first) content.push(first);
    let next = index + 1;
    while (next < lines.length) {
      const line = lines[next];
      const closeAt = line.indexOf(closer);
      if (closeAt !== -1) {
        content.push(line.slice(0, closeAt));
        return { tex: content.join("\n").trim(), next: next + 1 };
      }
      content.push(line);
      next += 1;
    }
    return { tex: content.join("\n").trim(), next };
  }

  function isTableStart(lines, index) {
    return Boolean(lines[index + 1] && /^\s*\|.+\|\s*$/.test(lines[index]) && /^\s*\|[-:\s|]+\|\s*$/.test(lines[index + 1]));
  }

  function renderTable(lines) {
    const rows = lines
      .filter((line) => /^\s*\|.*\|\s*$/.test(line))
      .map((line) => line.trim().split("|").slice(1, -1).map((cell) => cell.trim()));
    const head = rows[0] || [];
    const body = rows.slice(2);
    return `<div class="table-wrap"><table><thead><tr>${head.map((cell) => `<th>${renderInlineMarkdown(cell)}</th>`).join("")}</tr></thead><tbody>${body
      .map((row) => `<tr>${row.map((cell) => `<td>${renderInlineMarkdown(cell)}</td>`).join("")}</tr>`)
      .join("")}</tbody></table></div>`;
  }

  function isSpecialBlockStart(lines, index) {
    const line = lines[index] || "";
    const trimmed = line.trim();
    return (
      trimmed.startsWith("```") ||
      isDisplayMathStart(line) ||
      /^#{1,3}\s/.test(trimmed) ||
      /^(\*|-)\s+/.test(trimmed) ||
      /^\d+\.\s+/.test(trimmed) ||
      /^>\s?/.test(trimmed) ||
      /^---+$/.test(trimmed) ||
      isTableStart(lines, index)
    );
  }

  function renderMarkdown(text = "") {
    const lines = String(text).replace(/\r\n/g, "\n").split("\n");
    const html = [];
    let i = 0;

    while (i < lines.length) {
      const line = lines[i];
      const trimmed = line.trim();
      if (!trimmed) {
        i += 1;
        continue;
      }
      if (trimmed.startsWith("```")) {
        const lang = trimmed.slice(3).trim();
        const code = [];
        i += 1;
        while (i < lines.length && !lines[i].trim().startsWith("```")) {
          code.push(lines[i]);
          i += 1;
        }
        if (i < lines.length) i += 1;
        html.push(`<pre><code data-lang="${escapeHtml(lang)}">${escapeHtml(code.join("\n"))}</code></pre>`);
        continue;
      }
      if (isDisplayMathStart(line)) {
        const math = collectDisplayMath(lines, i);
        html.push(renderMath(math.tex, true));
        i = math.next;
        continue;
      }
      if (/^#{1,3}\s/.test(trimmed)) {
        const level = Math.min(trimmed.match(/^#+/)[0].length, 3);
        html.push(`<h${level}>${renderInlineMarkdown(trimmed.replace(/^#{1,3}\s*/, ""))}</h${level}>`);
        i += 1;
        continue;
      }
      if (/^(\*|-)\s+/.test(trimmed) || /^\d+\.\s+/.test(trimmed)) {
        const ordered = /^\d+\.\s+/.test(trimmed);
        const items = [];
        while (i < lines.length && (ordered ? /^\s*\d+\.\s+/.test(lines[i]) : /^\s*(\*|-)\s+/.test(lines[i]))) {
          items.push(lines[i].replace(ordered ? /^\s*\d+\.\s+/ : /^\s*(\*|-)\s+/, ""));
          i += 1;
        }
        const tag = ordered ? "ol" : "ul";
        html.push(`<${tag}>${items.map((item) => `<li>${renderInlineMarkdown(item)}</li>`).join("")}</${tag}>`);
        continue;
      }
      if (isTableStart(lines, i)) {
        const tableLines = [];
        while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) {
          tableLines.push(lines[i]);
          i += 1;
        }
        html.push(renderTable(tableLines));
        continue;
      }
      if (/^>\s?/.test(trimmed)) {
        const quote = [];
        while (i < lines.length && /^>\s?/.test(lines[i].trim())) {
          quote.push(lines[i].trim().replace(/^>\s?/, ""));
          i += 1;
        }
        html.push(`<blockquote>${quote.map(renderInlineMarkdown).join("<br>")}</blockquote>`);
        continue;
      }
      if (/^---+$/.test(trimmed)) {
        html.push("<hr>");
        i += 1;
        continue;
      }
      const paragraph = [];
      while (i < lines.length && lines[i].trim() && !isSpecialBlockStart(lines, i)) {
        paragraph.push(lines[i]);
        i += 1;
      }
      html.push(`<p>${paragraph.map(renderInlineMarkdown).join("<br>")}</p>`);
    }
    return html.join("");
  }

  return {
    renderMarkdown,
    renderInlineMarkdown,
    renderMath,
    escapeHtml,
  };
});
