/* Browser-side exam safeguards.
 *
 *  - Detects tab switches (visibilitychange), window focus loss (blur) and
 *    leaving full screen (fullscreenchange) and reports them once each,
 *    throttled so one action never produces a burst of violations.
 *  - Blocks right-click, text selection, drag, copy/cut/paste and common
 *    shortcuts (Ctrl+C/V/X/A/U/S/P, F12, Ctrl+Shift+I/J/C).
 *  - Safe Exam Browser (SEB) already runs in a locked kiosk window, so the
 *    full-screen requirement is skipped there automatically.
 *
 * These measures deter casual cheating; they cannot stop a determined user
 * on an unmanaged PC. Use SEB or OS-level lockdown for high-stakes tests.
 */
(function () {
  "use strict";

  var BLOCKED_CTRL_KEYS = ["c", "v", "x", "a", "u", "s", "p"];
  var BLOCKED_CTRL_SHIFT_KEYS = ["i", "j", "c", "k"];

  function isSafeExamBrowser() {
    return /SEB[\/ ]/i.test(navigator.userAgent);
  }

  window.CBTGuard = {
    init: function (opts) {
      var seb = isSafeExamBrowser();
      var fullscreenSupported = !!(document.fullscreenEnabled && opts.requireFullscreen && !seb);
      var lastReport = 0;
      var blurTimer = null;

      function active() {
        return typeof opts.isActive === "function" ? opts.isActive() : true;
      }

      function report(type, details) {
        if (!active()) return;
        var now = Date.now();
        if (now - lastReport < 1500) return;
        lastReport = now;
        opts.onViolation(type, details);
      }

      document.addEventListener("visibilitychange", function () {
        if (document.hidden) report("TAB_SWITCH", "Exam page hidden (tab/app switch or minimised).");
      });

      window.addEventListener("blur", function () {
        clearTimeout(blurTimer);
        // Let visibilitychange win if the whole page was hidden.
        blurTimer = setTimeout(function () {
          if (!document.hidden && !document.hasFocus()) report("WINDOW_BLUR", "Exam window lost focus.");
        }, 250);
      });

      if (fullscreenSupported) {
        document.addEventListener("fullscreenchange", function () {
          if (fullscreenSupported && !document.fullscreenElement) {
            report("FULLSCREEN_EXIT", "Left full-screen mode.");
            if (typeof opts.onFullscreenLost === "function") opts.onFullscreenLost();
          }
        });
      }

      function stop(evt) {
        evt.preventDefault();
        evt.stopPropagation();
        return false;
      }
      ["contextmenu", "copy", "cut", "paste", "dragstart", "selectstart", "drop"].forEach(function (name) {
        document.addEventListener(name, stop, true);
      });

      document.addEventListener("keydown", function (evt) {
        var key = (evt.key || "").toLowerCase();
        var ctrl = evt.ctrlKey || evt.metaKey;
        if (key === "f12" || key === "printscreen" ||
            (ctrl && evt.shiftKey && BLOCKED_CTRL_SHIFT_KEYS.indexOf(key) !== -1) ||
            (ctrl && !evt.shiftKey && BLOCKED_CTRL_KEYS.indexOf(key) !== -1)) {
          stop(evt);
        }
      }, true);

      return {
        seb: seb,
        isFullscreen: function () {
          return !fullscreenSupported || !!document.fullscreenElement;
        },
        /* Resolves true when full screen is active (or not required). If the
         * browser refuses (policy, kiosk shell, embedded view) we fail OPEN:
         * the requirement is dropped for this page so the student is never
         * locked out of their test; tab/blur detection keeps working. */
        enterFullscreen: function () {
          if (!fullscreenSupported || document.fullscreenElement) return Promise.resolve(true);
          var request = document.documentElement.requestFullscreen({ navigationUI: "hide" })
            .then(function () { return true; });
          // Some shells leave the request pending forever: treat 3 s of silence as a refusal.
          var timeout = new Promise(function (_, reject) {
            setTimeout(function () { reject(new Error("fullscreen timeout")); }, 3000);
          });
          return Promise.race([request, timeout]).catch(function () {
            if (document.fullscreenElement) return true;
            fullscreenSupported = false;
            return false;
          });
        }
      };
    }
  };
})();
