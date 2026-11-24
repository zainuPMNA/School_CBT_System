/* Offline KaTeX bootstrap. Renders LaTeX inside any element marked [data-math]
 * and after every HTMX swap. Expose window.cbtRenderMath(el) for live previews. */
(function () {
  "use strict";
  var OPTIONS = {
    delimiters: [
      { left: "$$", right: "$$", display: true },
      { left: "\\[", right: "\\]", display: true },
      { left: "\\(", right: "\\)", display: false },
      { left: "$", right: "$", display: false }
    ],
    throwOnError: false,
    strict: "ignore",
    trust: false,
    ignoredTags: ["script", "noscript", "style", "textarea", "pre", "code", "option", "input"]
  };

  window.cbtRenderMath = function (el) {
    if (typeof window.renderMathInElement !== "function") return;
    try {
      window.renderMathInElement(el || document.body, OPTIONS);
    } catch (err) {
      if (window.console) console.warn("KaTeX render failed", err);
    }
  };

  function renderMarked(root) {
    var scope = root || document;
    if (scope.matches && scope.matches("[data-math]")) window.cbtRenderMath(scope);
    var nodes = scope.querySelectorAll ? scope.querySelectorAll("[data-math]") : [];
    for (var i = 0; i < nodes.length; i++) window.cbtRenderMath(nodes[i]);
  }

  document.addEventListener("DOMContentLoaded", function () { renderMarked(document); });
  document.addEventListener("htmx:afterSwap", function (evt) { renderMarked(evt.detail.target); });
})();
