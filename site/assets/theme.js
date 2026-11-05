/* 在首屏绘制前决定深浅色，避免闪烁 */
(function () {
  try {
    var stored = localStorage.getItem("dd-theme");
    var dark = stored
      ? stored === "dark"
      : window.matchMedia("(prefers-color-scheme: dark)").matches;
    document.documentElement.dataset.theme = dark ? "dark" : "light";
  } catch (error) {
    document.documentElement.dataset.theme = "light";
  }
})();
