/*
 * static/js/goals.js
 * Small Alpine components for the Goals screens. Plain global functions (not Alpine.data),
 * so they work no matter when Alpine starts. Load this file with a normal (not deferred)
 * script tag BEFORE the markup that uses it.
 *
 *   goalToday()  wraps the Today list: progress ring numbers, listens for row events.
 *   goalRow()    one goal: optimistic check box, streak number, private note box.
 *   goalForm()   the goal form: All day, Repeat and Ends switches.
 *
 * The browser never sends a date. The server decides "today" and replies with it; if the
 * reply says a new day began, the page reloads. Errors reload the page too, so the server's
 * message shows as a toast (Django messages).
 */
(function () {
  "use strict";

  function csrfOf(el) {
    var holder = el.closest("[data-csrf]");
    return holder ? holder.dataset.csrf : "";
  }

  function post(url, fields, csrf) {
    return fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "X-CSRFToken": csrf,
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded"
      },
      body: new URLSearchParams(fields)
    }).then(function (response) {
      return response.json().catch(function () { return { ok: false }; });
    });
  }

  window.goalToday = function () {
    return {
      doneCount: 0,
      totalCount: 0,
      today: "",
      init: function () {
        this.doneCount = parseInt(this.$el.dataset.done || "0", 10);
        this.totalCount = parseInt(this.$el.dataset.total || "0", 10);
        this.today = this.$el.dataset.today || "";
      },
      get percent() {
        return this.totalCount ? Math.round(this.doneCount * 100 / this.totalCount) : 0;
      },
      onDelta: function (event) {
        this.doneCount = Math.max(0, Math.min(this.totalCount, this.doneCount + event.detail.delta));
      },
      onCounts: function (event) {
        this.doneCount = event.detail.done;
        this.totalCount = event.detail.total;
      }
    };
  };

  window.goalRow = function () {
    return {
      done: false,
      busy: false,
      noteOpen: false,
      note: "",
      noteState: "",
      streak: 0,
      init: function () {
        var d = this.$el.dataset;
        this.done = d.done === "1";
        this.note = d.note || "";
        this.streak = parseInt(d.streak || "0", 10);
        this.noteOpen = false;
      },
      toggle: function () {
        if (this.busy) { return; }
        var self = this;
        var wanted = !this.done;
        var csrf = csrfOf(this.$el);
        var today = (this.$el.closest("[data-today]") || {dataset: {}}).dataset.today;
        this.done = wanted;                       // optimistic: show it at once
        this.busy = true;
        this.$dispatch("goal-delta", { delta: wanted ? 1 : -1 });
        post(this.$el.dataset.checkUrl, { done: wanted ? "1" : "0" }, csrf).then(function (reply) {
          self.busy = false;
          if (!reply.ok) { window.location.reload(); return; }
          self.done = reply.done;
          self.streak = reply.streak;
          self.$dispatch("goal-counts", { done: reply.done_count, total: reply.total });
          if (today && reply.today && reply.today !== today) { window.location.reload(); }
        }).catch(function () { window.location.reload(); });
      },
      saveNote: function () {
        var self = this;
        this.noteState = "Saving...";
        post(this.$el.dataset.noteUrl, { note: this.note }, csrfOf(this.$el)).then(function (reply) {
          if (!reply.ok) { window.location.reload(); return; }
          self.noteState = "Saved";
        }).catch(function () { self.noteState = "Could not save. Try again."; });
      }
    };
  };

  window.goalForm = function () {
    return {
      allDay: true,
      repeat: "daily",
      ends: "never",
      init: function () {
        var d = this.$el.dataset;
        this.allDay = d.allDay === "1";
        this.repeat = d.repeat || "daily";
        this.ends = d.ends || "never";
      }
    };
  };
})();
