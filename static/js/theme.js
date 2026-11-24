/* Screen theme switcher: Light / Dark / Warm.
 * - Applies the theme instantly (data-theme on <html>).
 * - Remembers it in this browser (for the login page).
 * - Saves it to the signed-in user's account, so each student keeps their own
 *   choice on shared lab PCs.
 * The early part (choosing the theme before the page paints) is inline in base.html.
 */
(function () {
  "use strict";
  var THEMES = ["light", "dark", "warm"];

  function csrf() {
    var m = document.cookie.match(/(?:^|;\s*)cbt_csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : "";
  }

  function apply(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    try { localStorage.setItem("cbt-theme", theme); } catch (e) { /* private mode */ }
  }

  function save(theme) {
    var body = new URLSearchParams({ theme: theme });
    fetch("/preferences/theme/", {
      method: "POST",
      credentials: "same-origin",
      headers: { "X-CSRFToken": csrf(), "X-Requested-With": "XMLHttpRequest", "Accept": "application/json" },
      body: body
    }).catch(function () { /* offline: the browser still remembers it */ });
  }

  document.addEventListener("click", function (evt) {
    var btn = evt.target.closest ? evt.target.closest("[data-set-theme]") : null;
    if (!btn) return;
    var theme = btn.getAttribute("data-set-theme");
    if (THEMES.indexOf(theme) === -1) return;
    evt.preventDefault();
    apply(theme);
    save(theme);
  });
})();
