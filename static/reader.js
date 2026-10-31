(function () {
  const PDFJS_URL = "/vendor/reader/pdfjs/pdf.mjs";
  const PDFJS_WORKER_URL = "/vendor/reader/pdfjs/pdf.worker.mjs";
  const MAX_RENDERED_PAGES = 7;
  const tr = (key, variables = {}) => window.StudyHubI18n?.t(key, variables) || key;

  class ReaderController {
    constructor() {
      this.generation = 0;
      this.zoom = 1.15;
      this.currentPage = 1;
      this.rendered = new Map();
      this.rendering = new Set();
      this.renderTasks = new Map();
      this.observer = null;
    }

    destroy() {
      this.generation += 1;
      this.observer?.disconnect();
      this.observer = null;
      this.loadingTask?.destroy?.();
      this.loadingTask = null;
      this.pdf = null;
      for (const task of this.renderTasks.values()) task.cancel();
      this.renderTasks.clear();
      this.rendered.clear();
      this.rendering.clear();
    }

    async open({ fileId, page = 1, mount, onPageChange }) {
      this.destroy();
      const generation = this.generation;
      this.mount = mount;
      this.fileId = Number(fileId);
      this.onPageChange = onPageChange;
      this.currentPage = Math.max(1, Number(page || 1));
      mount.innerHTML = `<div class="reader-loading" role="status">${tr("reader.loading")}</div>`;
      const response = await fetch(`/api/reader?id=${encodeURIComponent(this.fileId)}`, { credentials: "same-origin" });
      if (!response.ok) throw new Error(`Reader metadata failed (${response.status})`);
      const metadata = await response.json();
      if (generation !== this.generation) return;
      this.metadata = metadata;
      if (metadata.visual_type === "pdf") return this.openPdf(metadata, generation);
      if (metadata.visual_type === "image") return this.openImage(metadata);
      return this.openText(metadata);
    }

    toolbar() {
      return `
        <div class="reader-toolbar" role="toolbar" aria-label="${tr("reader.controls")}">
          <button class="icon-button" data-reader-action="previous" title="${tr("reader.previous")}" aria-label="${tr("reader.previous")}">‹</button>
          <label class="reader-page-label"><span class="sr-only">${tr("reader.page")}</span><input data-reader-page type="number" min="1" value="${this.currentPage}" /></label>
          <span data-reader-count aria-live="polite">/ —</span>
          <button class="icon-button" data-reader-action="next" title="${tr("reader.next")}" aria-label="${tr("reader.next")}">›</button>
          <span class="reader-divider"></span>
          <button class="icon-button" data-reader-action="zoom-out" title="${tr("reader.zoomOut")}" aria-label="${tr("reader.zoomOut")}">−</button>
          <span data-reader-zoom>${Math.round(this.zoom * 100)}%</span>
          <button class="icon-button" data-reader-action="zoom-in" title="${tr("reader.zoomIn")}" aria-label="${tr("reader.zoomIn")}">+</button>
          <button class="button secondary compact" data-reader-action="fit">${tr("reader.fitWidth")}</button>
          <button class="button secondary compact" data-reader-action="fit-page">${tr("reader.fitPage")}</button>
          <label class="reader-search"><span class="sr-only">${tr("reader.find")}</span><input data-reader-search type="search" placeholder="${tr("reader.find")}" /></label>
          <button class="icon-button" data-reader-action="find" title="${tr("reader.findNext")}" aria-label="${tr("reader.findNext")}">⌕</button>
        </div>
        <div class="reader-status" data-reader-status aria-live="polite"></div>`;
    }

    async openPdf(metadata, generation) {
      this.mount.innerHTML = `<div class="reader-shell">${this.toolbar()}<div class="reader-pages" data-reader-pages></div></div>`;
      this.bindToolbar();
      try {
        const pdfjs = await import(PDFJS_URL);
        pdfjs.GlobalWorkerOptions.workerSrc = PDFJS_WORKER_URL;
        this.pdfjs = pdfjs;
        const task = pdfjs.getDocument({
          url: metadata.preview_url,
          isEvalSupported: false,
          enableXfa: false,
          useSystemFonts: true,
        });
        this.loadingTask = task;
        this.pdf = await task.promise;
        if (generation !== this.generation) return;
        this.mount.querySelector("[data-reader-count]").textContent = `/ ${this.pdf.numPages}`;
        this.mount.querySelector("[data-reader-page]").max = String(this.pdf.numPages);
        await this.buildPagePlaceholders();
        this.goToPage(Math.min(this.currentPage, this.pdf.numPages), false);
      } catch (error) {
        this.showTextFallback(error);
      }
    }

    async buildPagePlaceholders() {
      const pages = this.mount.querySelector("[data-reader-pages]");
      const first = await this.pdf.getPage(1);
      const viewport = first.getViewport({ scale: this.zoom });
      for (let page = 1; page <= this.pdf.numPages; page += 1) {
        const holder = document.createElement("section");
        holder.className = "reader-page";
        holder.dataset.page = String(page);
        holder.setAttribute("aria-label", tr("reader.pageStatus", { page }));
        holder.style.aspectRatio = `${viewport.width} / ${viewport.height}`;
        holder.innerHTML = `<div class="reader-page-number">${page}</div>`;
        pages.appendChild(holder);
      }
      this.observer = new IntersectionObserver((entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          const page = Number(entry.target.dataset.page);
          this.setCurrentPage(page);
          for (let candidate = Math.max(1, page - 1); candidate <= Math.min(this.pdf.numPages, page + 1); candidate += 1) {
            this.renderPage(candidate);
          }
        }
        this.trimRendered();
      }, { root: pages, rootMargin: "80% 0px", threshold: 0.2 });
      pages.querySelectorAll(".reader-page").forEach((node) => this.observer.observe(node));
    }

    async renderPage(pageNumber) {
      if (this.rendered.has(pageNumber) || this.rendering.has(pageNumber) || !this.pdf) return;
      this.rendering.add(pageNumber);
      const holder = this.mount.querySelector(`.reader-page[data-page="${pageNumber}"]`);
      if (!holder) {
        this.rendering.delete(pageNumber);
        return;
      }
      const generation = this.generation;
      try {
        const page = await this.pdf.getPage(pageNumber);
        const viewport = page.getViewport({ scale: this.zoom });
        const ratio = Math.min(window.devicePixelRatio || 1, 2);
        const canvas = document.createElement("canvas");
        canvas.width = Math.floor(viewport.width * ratio);
        canvas.height = Math.floor(viewport.height * ratio);
        canvas.style.width = `${viewport.width}px`;
        canvas.style.height = `${viewport.height}px`;
        const textLayer = document.createElement("div");
        textLayer.className = "reader-text-layer";
        holder.replaceChildren(canvas, textLayer);
        holder.style.width = `${viewport.width}px`;
        holder.style.height = `${viewport.height}px`;
        holder.style.aspectRatio = "auto";
        const renderTask = page.render({ canvasContext: canvas.getContext("2d"), viewport, transform: ratio === 1 ? null : [ratio, 0, 0, ratio, 0, 0], annotationMode: 0 });
        this.renderTasks.set(pageNumber, renderTask);
        await renderTask.promise;
        if (generation !== this.generation) return;
        const textContent = await page.getTextContent();
        if (generation !== this.generation) return;
        const layer = new this.pdfjs.TextLayer({ textContentSource: textContent, container: textLayer, viewport });
        await layer.render();
        this.rendered.set(pageNumber, { page, holder });
      } catch (error) {
        if (error?.name !== "RenderingCancelledException") throw error;
      } finally {
        this.renderTasks.delete(pageNumber);
        this.rendering.delete(pageNumber);
      }
    }

    trimRendered() {
      if (this.rendered.size <= MAX_RENDERED_PAGES) return;
      const ordered = [...this.rendered.keys()].sort((a, b) => Math.abs(b - this.currentPage) - Math.abs(a - this.currentPage));
      while (this.rendered.size > MAX_RENDERED_PAGES) {
        const page = ordered.shift();
        const record = this.rendered.get(page);
        if (!record) continue;
        record.page.cleanup();
        record.holder.innerHTML = `<div class="reader-page-number">${page}</div>`;
        this.rendered.delete(page);
      }
    }

    bindToolbar() {
      this.mount.querySelector("[data-reader-page]").addEventListener("change", (event) => this.goToPage(Number(event.target.value)));
      this.mount.querySelector("[data-reader-search]").addEventListener("keydown", (event) => {
        if (event.key === "Enter") this.findNext();
      });
      this.mount.querySelectorAll("[data-reader-action]").forEach((button) => button.addEventListener("click", () => {
        const action = button.dataset.readerAction;
        if (action === "previous") this.goToPage(this.currentPage - 1);
        if (action === "next") this.goToPage(this.currentPage + 1);
        if (action === "zoom-in") this.setZoom(this.zoom + 0.15);
        if (action === "zoom-out") this.setZoom(this.zoom - 0.15);
        if (action === "fit") this.fitWidth();
        if (action === "fit-page") this.fitPage();
        if (action === "find") this.findNext();
      }));
      this.mount.tabIndex = 0;
      this.mount.addEventListener("keydown", (event) => {
        if (event.target.matches("input, textarea")) return;
        if (event.key === "PageDown" || event.key === "ArrowRight") this.goToPage(this.currentPage + 1);
        if (event.key === "PageUp" || event.key === "ArrowLeft") this.goToPage(this.currentPage - 1);
        if (event.key === "+" || event.key === "=") this.setZoom(this.zoom + 0.15);
        if (event.key === "-") this.setZoom(this.zoom - 0.15);
      });
    }

    setCurrentPage(page) {
      if (page === this.currentPage) return;
      this.currentPage = page;
      const input = this.mount.querySelector("[data-reader-page]");
      if (input) input.value = String(page);
      this.onPageChange?.(page);
    }

    goToPage(page, announce = true) {
      const target = Math.max(1, Math.min(Number(page || 1), this.pdf?.numPages || 1));
      this.currentPage = target;
      this.mount.querySelector("[data-reader-page]").value = String(target);
      this.mount.querySelector(`.reader-page[data-page="${target}"]`)?.scrollIntoView({ block: "start" });
      this.renderPage(target);
      this.onPageChange?.(target);
      if (announce) this.status(tr("reader.pageStatus", { page: target }));
    }

    async setZoom(value) {
      this.zoom = Math.max(0.5, Math.min(3, value));
      this.mount.querySelector("[data-reader-zoom]").textContent = `${Math.round(this.zoom * 100)}%`;
      for (const task of this.renderTasks.values()) task.cancel();
      this.renderTasks.clear();
      this.rendering.clear();
      for (const { page, holder } of this.rendered.values()) {
        page.cleanup();
        holder.innerHTML = `<div class="reader-page-number">${holder.dataset.page}</div>`;
      }
      this.rendered.clear();
      await this.renderPage(this.currentPage);
      this.goToPage(this.currentPage, false);
    }

    async fitWidth() {
      if (!this.pdf) return;
      const page = await this.pdf.getPage(this.currentPage);
      const base = page.getViewport({ scale: 1 });
      const width = this.mount.querySelector("[data-reader-pages]").clientWidth - 32;
      await this.setZoom(width / base.width);
    }

    async fitPage() {
      if (!this.pdf) return;
      const page = await this.pdf.getPage(this.currentPage);
      const base = page.getViewport({ scale: 1 });
      const pages = this.mount.querySelector("[data-reader-pages]");
      const widthScale = (pages.clientWidth - 32) / base.width;
      const heightScale = (pages.clientHeight - 32) / base.height;
      await this.setZoom(Math.min(widthScale, heightScale));
    }

    async findNext() {
      const query = this.mount.querySelector("[data-reader-search]").value.trim().toLocaleLowerCase();
      if (!query || !this.pdf) return;
      for (let offset = 1; offset <= this.pdf.numPages; offset += 1) {
        const number = ((this.currentPage - 1 + offset) % this.pdf.numPages) + 1;
        const page = await this.pdf.getPage(number);
        const content = await page.getTextContent();
        if (content.items.map((item) => item.str || "").join(" ").toLocaleLowerCase().includes(query)) {
          this.goToPage(number);
          this.status(tr("reader.foundPage", { page: number }));
          return;
        }
      }
      this.status(tr("reader.noMatch"));
    }

    openImage(metadata) {
      this.mount.innerHTML = `<div class="reader-image-wrap"><img alt="${this.escape(metadata.filename)}" src="${metadata.preview_url}" /></div>`;
    }

    openText(metadata) {
      const language = this.languageFor(metadata.extension);
      this.mount.innerHTML = `<div class="reader-text-wrap"><pre><code class="language-${language}"></code></pre></div>`;
      const code = this.mount.querySelector("code");
      code.textContent = metadata.readable_text || tr("reader.noReadableText");
      window.Prism?.highlightElement(code);
    }

    showTextFallback(error) {
      this.openText(this.metadata);
      const notice = document.createElement("div");
      notice.className = "reader-fallback-notice";
      notice.textContent = tr("reader.visualUnavailable");
      this.mount.prepend(notice);
      console.warn("Reader visual preview unavailable", error?.message || "unknown error");
    }

    languageFor(extension) {
      return ({ ".py": "python", ".r": "r", ".json": "json", ".ipynb": "json", ".md": "markdown" })[extension] || "none";
    }

    status(message) {
      const node = this.mount.querySelector("[data-reader-status]");
      if (node) node.textContent = message;
    }

    escape(value) {
      return String(value || "").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[char]);
    }
  }

  window.StudyHubReader = new ReaderController();
})();
