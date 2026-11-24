/* NTA-style exam screen (Alpine.js component).
 *
 * Timer   : the server sends `remaining` seconds; the browser counts down with
 *           performance.now() (monotonic — changing the PC clock has no effect)
 *           and re-syncs with the server every `syncInterval` seconds and after
 *           every save.
 * Saving  : every click queues the question's full state; a single-flight
 *           queue posts it, coalescing rapid clicks and retrying with back-off
 *           when the network drops. Unsaved states are also sent with Submit.
 * Options : the browser only knows on-screen positions 0-3; the server maps
 *           them to the real option keys.
 */
function cbtExam() {
  "use strict";
  var cfg = JSON.parse(document.getElementById("exam-config").textContent);
  var ANSWERED = "ANSWERED", ANSWERED_MARKED = "ANSWERED_MARKED", MARKED = "MARKED_REVIEW",
      NOT_ANSWERED = "NOT_ANSWERED", NOT_VISITED = "NOT_VISITED";

  function resolveStatus(selected, requested) {
    var marked = requested === MARKED || requested === ANSWERED_MARKED;
    if (selected !== null && selected !== undefined) return marked ? ANSWERED_MARKED : ANSWERED;
    if (marked) return MARKED;
    if (requested === ANSWERED) return NOT_ANSWERED;
    return requested;
  }

  return {
    questions: cfg.questions,
    current: Math.max(0, Math.min(cfg.current || 0, cfg.questions.length - 1)),
    remaining: cfg.remaining,
    deadline: 0,
    pending: new Map(),
    inFlight: false,
    retryDelay: 1000,
    saveState: "saved",
    locked: false,
    paused: false,
    timeUp: false,
    showSubmit: false,
    submitMessage: "Please wait, do not close this window.",
    zoomSrc: null,
    needFullscreen: false,
    violation: { show: false, count: cfg.violations || 0, message: "" },
    guard: null,

    /* ------------------------------------------------------------ setup */
    init: function () {
      var self = this;
      this.setDeadline(this.remaining, 0);
      setInterval(function () { self.tick(); }, 500);
      setInterval(function () { self.sync(); }, Math.max(5, cfg.syncInterval || 15) * 1000);
      window.addEventListener("online", function () { self.flush(); });
      window.addEventListener("beforeunload", function (evt) {
        if (self.pending.size && !self.locked) { evt.preventDefault(); evt.returnValue = ""; }
      });

      if (window.CBTGuard) {
        this.guard = window.CBTGuard.init({
          requireFullscreen: cfg.requireFullscreen,
          isActive: function () { return !self.locked; },
          onViolation: function (type, details) { self.recordViolation(type, details); },
          onFullscreenLost: function () { self.needFullscreen = true; }
        });
        this.needFullscreen = !this.guard.isFullscreen();
      }
      this.visit(this.current);
    },

    /* ------------------------------------------------------------ getters */
    get q() { return this.questions[this.current]; },

    get counts() {
      var c = { ANSWERED: 0, NOT_ANSWERED: 0, NOT_VISITED: 0, MARKED_REVIEW: 0, ANSWERED_MARKED: 0 };
      for (var i = 0; i < this.questions.length; i++) c[this.questions[i].status] += 1;
      return c;
    },

    get timeText() {
      var s = Math.max(0, this.remaining);
      var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
      return [h, m, sec].map(function (n) { return n < 10 ? "0" + n : "" + n; }).join(":");
    },

    paletteClass: function (item, index) {
      var map = {
        ANSWERED: "palette-answered",
        NOT_ANSWERED: "palette-not-answered",
        NOT_VISITED: "palette-not-visited",
        MARKED_REVIEW: "palette-marked",
        ANSWERED_MARKED: "palette-answered-marked"
      };
      return map[item.status] + (index === this.current ? " palette-current" : "");
    },

    /* ------------------------------------------------------------ timer */
    setDeadline: function (remainingSeconds, latencyMs) {
      this.deadline = performance.now() + remainingSeconds * 1000 - (latencyMs || 0) / 2;
      this.tick();
    },

    tick: function () {
      this.remaining = Math.max(0, Math.ceil((this.deadline - performance.now()) / 1000));
      if (this.remaining <= 0 && !this.locked) this.onTimeUp();
    },

    sync: function () {
      var self = this;
      if (this.locked) return;
      var started = performance.now();
      fetch(cfg.urls.sync + "?pos=" + this.current, { headers: this.headers(), credentials: "same-origin" })
        .then(function (r) { return self.handleResponse(r); })
        .then(function (data) {
          if (!data) return;
          self.setDeadline(data.remaining, performance.now() - started);
          self.paused = !data.exam_active;
          if (typeof data.violations === "number") self.violation.count = data.violations;
          if (!self.paused) self.flush();
        })
        .catch(function () { /* offline: local countdown keeps running */ });
    },

    /* ------------------------------------------------------------ networking */
    headers: function () {
      return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-CSRFToken": this.csrfToken(),
        "X-Requested-With": "XMLHttpRequest"
      };
    },

    /* Always send the CURRENT token: it changes whenever anyone signs in on this
       browser, and a token captured at page load would then make saves fail. */
    csrfToken: function () {
      var match = document.cookie.match(/(?:^|;\s*)cbt_csrftoken=([^;]+)/);
      return match ? decodeURIComponent(match[1]) : cfg.csrf;
    },

    post: function (url, payload) {
      return fetch(url, {
        method: "POST",
        headers: this.headers(),
        credentials: "same-origin",
        body: JSON.stringify(payload)
      });
    },

    /* Returns parsed JSON for 2xx, handles terminal states, throws on retryable failures. */
    handleResponse: function (response) {
      var self = this;
      return response.json().catch(function () { return {}; }).then(function (data) {
        if (response.ok) return data;
        if (response.status === 401) { window.location.href = data.redirect || "/login/?reason=session_ended"; return null; }
        if (response.status === 409) { self.locked = true; window.location.href = data.redirect || cfg.urls.submitted; return null; }
        if (response.status === 423) {
          self.paused = true;
          if (typeof data.remaining === "number") self.setDeadline(data.remaining, 0);
          var paused = new Error("paused"); paused.paused = true; throw paused;
        }
        if (response.status === 400) { var bad = new Error(data.message || "bad request"); bad.fatal = true; throw bad; }
        throw new Error("HTTP " + response.status);
      });
    },

    queue: function (item) {
      this.pending.set(item.id, {
        question_id: item.id,
        selected: item.selected,
        status: item.status,
        current_index: this.current
      });
      this.flush();
    },

    flush: function () {
      var self = this;
      if (this.inFlight || !this.pending.size || this.locked) return;
      var entry = this.pending.entries().next().value;
      var id = entry[0], payload = entry[1];
      this.pending.delete(id);
      this.inFlight = true;
      this.saveState = "saving";
      var started = performance.now();

      this.post(cfg.urls.save, payload)
        .then(function (r) { return self.handleResponse(r); })
        .then(function (data) {
          self.inFlight = false;
          if (!data) return;
          self.retryDelay = 1000;
          self.paused = false;
          if (typeof data.remaining === "number") self.setDeadline(data.remaining, performance.now() - started);
          self.saveState = self.pending.size ? "saving" : "saved";
          self.flush();
        })
        .catch(function (err) {
          self.inFlight = false;
          if (err && err.fatal) {             // rejected payload: drop it, keep going
            self.saveState = self.pending.size ? "saving" : "saved";
            self.flush();
            return;
          }
          if (!self.pending.has(id)) self.pending.set(id, payload);  // keep newest state
          if (err && err.paused) { self.saveState = "saving"; return; } // resumes via sync()
          self.saveState = "error";
          setTimeout(function () { self.flush(); }, self.retryDelay);
          self.retryDelay = Math.min(self.retryDelay * 2, 15000);
        });
    },

    /* ------------------------------------------------------------ answer actions */
    setState: function (index, selected, requested) {
      var item = this.questions[index];
      var status = resolveStatus(selected, requested);
      if (item.selected === selected && item.status === status) return;
      item.selected = selected;
      item.status = status;
      this.queue(item);
    },

    select: function (position) {
      if (this.locked || this.paused) return;
      var item = this.q;
      var marked = item.status === MARKED || item.status === ANSWERED_MARKED;
      this.setState(this.current, position, marked ? ANSWERED_MARKED : ANSWERED);
    },

    clearResponse: function () {
      if (this.locked || this.paused) return;
      this.setState(this.current, null, NOT_ANSWERED);
    },

    saveNext: function () {
      if (this.locked || this.paused) return;
      var item = this.q;
      this.setState(this.current, item.selected, item.selected === null ? NOT_ANSWERED : ANSWERED);
      this.next();
    },

    markNext: function () {
      if (this.locked || this.paused) return;
      this.setState(this.current, this.q.selected, MARKED);
      this.next();
    },

    visit: function (index) {
      this.current = index;
      var item = this.questions[index];
      if (item && item.status === NOT_VISITED) this.setState(index, item.selected, NOT_ANSWERED);
      var main = document.querySelector("main .overflow-y-auto");
      if (main) main.scrollTop = 0;
    },

    goTo: function (index) { if (!this.locked) this.visit(index); },
    next: function () { if (this.current < this.questions.length - 1) this.visit(this.current + 1); },
    prev: function () { if (this.current > 0) this.visit(this.current - 1); },

    /* ------------------------------------------------------------ anti-cheat */
    recordViolation: function (type, details) {
      var self = this;
      var messages = {
        TAB_SWITCH: "You left the test page (switched tab or application).",
        WINDOW_BLUR: "The test window lost focus.",
        FULLSCREEN_EXIT: "You left full-screen mode."
      };
      this.violation.count += 1;
      this.violation.message = messages[type] || "Suspicious activity detected.";
      this.violation.show = true;
      this.post(cfg.urls.violation, { type: type, details: details })
        .then(function (r) { return self.handleResponse(r); })
        .then(function (data) { if (data && typeof data.violations === "number") self.violation.count = data.violations; })
        .catch(function () { /* best effort */ });
    },

    dismissViolation: function () {
      this.violation.show = false;
      if (this.guard && !this.guard.isFullscreen()) this.needFullscreen = true;
    },

    enterFullscreen: function () {
      var self = this;
      if (!this.guard) { this.needFullscreen = false; return; }
      this.guard.enterFullscreen().then(function () {
        // isFullscreen() is also true when the browser refused and the guard failed open.
        self.needFullscreen = !self.guard.isFullscreen();
        self.violation.show = false;
      });
    },

    /* ------------------------------------------------------------ submit */
    openSubmit: function () { if (!this.locked) this.showSubmit = true; },

    onTimeUp: function () {
      this.timeUp = true;
      this.submitMessage = "Your answers are being submitted automatically.";
      this.submit(true);
    },

    submit: function (auto) {
      var self = this;
      if (this.locked && !this.timeUp) return;
      if (this._submitting) return;
      this._submitting = true;
      this.locked = true;
      this.showSubmit = false;
      var answers = Array.from(this.pending.values());

      var attempt = function () {
        self.post(cfg.urls.submit, { auto: !!auto, answers: answers })
          .then(function (r) { return r.json().then(function (d) { return { status: r.status, data: d }; }); })
          .then(function (res) {
            if (res.status === 200 || res.status === 409) {
              self.pending.clear();
              window.location.href = (res.data && res.data.redirect) || cfg.urls.submitted;
            } else if (res.status === 401) {
              window.location.href = (res.data && res.data.redirect) || "/login/";
            } else {
              throw new Error("HTTP " + res.status);
            }
          })
          .catch(function () {
            self.submitMessage = "Network problem — retrying… Do NOT close this window. Call the invigilator if this continues.";
            setTimeout(attempt, 3000);
          });
      };
      attempt();
    }
  };
}
