const invoke = window.__TAURI__?.core?.invoke;
const retryButton = document.querySelector("#retryButton");
const copyButton = document.querySelector("#copyButton");
const status = document.querySelector("#status");
const diagnostics = document.querySelector("#diagnostics");

retryButton.addEventListener("click", async () => {
  retryButton.disabled = true;
  status.textContent = "Starting the local service...";
  try {
    await invoke("retry_backend");
  } catch (error) {
    status.textContent = error || "StudyHub could not start its local service.";
    retryButton.disabled = false;
  }
});

copyButton.addEventListener("click", async () => {
  try {
    const text = await invoke("startup_diagnostics");
    diagnostics.value = text;
    diagnostics.select();
    await navigator.clipboard.writeText(text);
    status.textContent = "Diagnostics copied.";
  } catch (_error) {
    status.textContent = "Diagnostics could not be copied.";
  }
});
