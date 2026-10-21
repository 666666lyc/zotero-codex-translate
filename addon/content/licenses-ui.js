/* global window, document */
window.addEventListener("DOMContentLoaded", () => {
  const data = window.arguments[0];
  document.getElementById("notices").textContent = data.notices;
  document.getElementById("license").textContent = data.license;
  const source = document.getElementById("source");
  if (data.sourceURL) {
    const link = document.createElement("a");
    link.href = data.sourceURL;
    link.textContent = "本版本对应源码";
    source.append(link);
  } else source.textContent = "本地开发预览；对应源码随本地 source.zip 提供，尚未公开发布。";
});
