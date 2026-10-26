/*
 * static/js/buddy_chat.js
 * The Alpine component for the chat on the Buddy page (right column from lg up). A plain global function (not Alpine.data), like goals.js,
 * so it works no matter when Alpine starts. Load it with a normal (not deferred) script tag BEFORE
 * the markup that uses it.
 *
 *   buddyChat()  one chat: shows the thread, sends messages, nudges and prayed taps, polls for new
 *                notes every ~10 seconds while the tab is visible, and tells the server when the
 *                thread has been read.
 *
 * The first page of the thread, the one-time token and the URLs come from the JSON block
 * #buddy-chat-data. The browser never decides who the partner is: every URL works on the signed-in
 * person's own partnership. Errors show inline in the chat, nothing is sent twice (the one-time token
 * makes a retry after a lost reply a quiet no-op).
 */
(function () {
  "use strict";

  var POLL_MS = 10000;

  function call(method, url, csrf, fields) {
    var options = {
      method: method,
      credentials: "same-origin",
      headers: { "Accept": "application/json", "X-Requested-With": "XMLHttpRequest" }
    };
    if (method === "POST") {
      options.headers["X-CSRFToken"] = csrf;
      options.headers["Content-Type"] = "application/x-www-form-urlencoded";
      options.body = new URLSearchParams(fields || {});
    }
    return fetch(url, options).then(function (response) {
      return response.json().catch(function () {
        return { ok: false, error: "Something went wrong. Try again." };
      }).then(function (data) { data.httpStatus = response.status; return data; });
    });
  }

  window.buddyChat = function () {
    return {
      items: [],
      keys: {},                // keys of items already shown, so a note never appears twice
      hasMore: false,
      oldestId: null,
      newestId: 0,
      readOnly: false,
      partnerName: "",
      lastMineId: null,
      lastMineSeen: false,
      text: "",
      token: "",
      busy: false,
      error: "",
      urls: {},
      csrf: "",
      nudgeUntil: 0,
      prayedToday: false,
      tick: 0,                 // bumped by the poll timer so the nudge countdown re-renders
      timer: null,
      onVisible: null,

      init: function () {
        var data = JSON.parse(document.getElementById("buddy-chat-data").textContent);
        var self = this;
        this.csrf = this.$el.dataset.csrf || "";
        this.urls = data.urls;
        this.token = data.token;
        this.readOnly = data.read_only;
        this.partnerName = data.partner_name;
        this.hasMore = data.has_more;
        this.oldestId = data.oldest_id;
        this.newestId = data.newest_id || 0;
        this.lastMineId = data.last_mine_id;
        this.lastMineSeen = data.last_mine_seen;
        this.prayedToday = data.prayed_today;
        this.setNudgeWait(data.nudge_wait);
        this.append(data.items);

        this.$nextTick(function () { self.scrollBottom(true); self.markRead(); });
        this.timer = setInterval(function () { self.tick += 1; self.poll(); }, POLL_MS);
        this.onVisible = function () {
          if (document.visibilityState === "visible") { self.poll(); self.markRead(); }
        };
        document.addEventListener("visibilitychange", this.onVisible);
      },

      destroy: function () {
        clearInterval(this.timer);
        document.removeEventListener("visibilitychange", this.onVisible);
      },

      // ------------------------------------------------------------ list helpers
      append: function (list) {
        for (var i = 0; i < list.length; i += 1) {
          var item = list[i];
          if (this.keys[item.key]) { continue; }
          this.keys[item.key] = true;
          this.items.push(item);
          if (item.type === "note" && item.id > this.newestId) { this.newestId = item.id; }
        }
      },
      nearBottom: function () {
        var list = this.$refs.list;
        return list.scrollHeight - list.scrollTop - list.clientHeight < 80;
      },
      scrollBottom: function (force) {
        var list = this.$refs.list;
        if (force || this.nearBottom()) {
          this.$nextTick(function () { list.scrollTop = list.scrollHeight; });
        }
      },
      // A day label shows above the first note of each day.
      showDay: function (index) {
        var item = this.items[index];
        if (!item || item.type !== "note") { return false; }
        for (var i = index - 1; i >= 0; i -= 1) {
          if (this.items[i].type === "note") { return this.items[i].day !== item.day; }
        }
        return true;
      },
      applySeen: function (data) {
        if (data.last_mine_id !== undefined) {
          this.lastMineId = data.last_mine_id;
          this.lastMineSeen = !!data.last_mine_seen;
        }
      },

      // ------------------------------------------------------------ nudge countdown
      setNudgeWait: function (seconds) {
        this.nudgeUntil = Date.now() + (seconds || 0) * 1000;
      },
      get nudgeMinutes() {
        this.tick;               // read it so Alpine re-evaluates this getter after each tick
        return Math.max(0, Math.ceil((this.nudgeUntil - Date.now()) / 60000));
      },

      // ------------------------------------------------------------ polling and reading
      poll: function () {
        var self = this;
        if (document.visibilityState !== "visible" || this.busy) { return; }
        call("GET", this.urls.poll + "?after=" + this.newestId, this.csrf).then(function (data) {
          if (!data.ok) { return; }
          if (!data.active) { window.location.reload(); return; }      // the partnership ended
          var stick = self.nearBottom();
          var fromPartner = data.notes.some(function (n) { return !n.mine; });
          self.readOnly = data.read_only;
          self.append(data.notes);
          self.applySeen(data);
          if (data.notes.length) { self.scrollBottom(stick); }
          if (fromPartner) { self.markRead(); }
        }).catch(function () { /* quiet: the next tick tries again */ });
      },
      markRead: function () {
        if (document.visibilityState !== "visible") { return; }
        call("POST", this.urls.read, this.csrf).catch(function () { /* quiet */ });
      },

      // ------------------------------------------------------------ earlier notes
      loadEarlier: function () {
        var self = this;
        var list = this.$refs.list;
        if (!this.oldestId || this.busy) { return; }
        var before = list.scrollHeight - list.scrollTop;
        this.busy = true;
        call("GET", this.urls.earlier + "?before=" + this.oldestId, this.csrf).then(function (data) {
          self.busy = false;
          if (!data.ok) { self.error = data.error || "Could not load earlier messages."; return; }
          var fresh = data.items.filter(function (item) { return !self.keys[item.key]; });
          fresh.forEach(function (item) { self.keys[item.key] = true; });
          self.items = fresh.concat(self.items);
          self.hasMore = data.has_more;
          if (data.oldest_id) { self.oldestId = data.oldest_id; }
          self.$nextTick(function () { list.scrollTop = list.scrollHeight - before; });  // keep the view still
        }).catch(function () {
          self.busy = false;
          self.error = "Could not load earlier messages.";
        });
      },

      // ------------------------------------------------------------ sending
      onEnter: function (event) {
        // Enter sends on a keyboard; on a phone (coarse pointer) it stays a line break.
        if (event.shiftKey || event.isComposing) { return; }
        if (window.matchMedia && window.matchMedia("(pointer: coarse)").matches) { return; }
        event.preventDefault();
        this.send();
      },
      resize: function () {
        var box = this.$refs.box;
        box.style.height = "auto";
        box.style.height = Math.min(box.scrollHeight, 128) + "px";
      },
      done: function (reply) {
        if (reply.token) { this.token = reply.token; }
        this.applySeen(reply);
        if (reply.note) { this.append([reply.note]); this.scrollBottom(true); }
      },
      fail: function (reply) {
        this.error = reply.error || "Something went wrong. Try again.";
        if (reply.token) { this.token = reply.token; }
      },
      send: function () {
        var self = this;
        var body = this.text.trim();
        if (!body || this.busy || this.readOnly) { return; }
        this.busy = true;
        this.error = "";
        call("POST", this.urls.send, this.csrf, { body: body, token: this.token }).then(function (reply) {
          self.busy = false;
          if (!reply.ok) { self.fail(reply); return; }
          self.text = "";
          self.$nextTick(function () { self.resize(); });
          self.done(reply);
          if (reply.duplicate) { self.poll(); }      // a replay: the first copy is already saved, fetch it
        }).catch(function () {
          self.busy = false;
          self.error = "Could not send. Check your connection and try again.";
        });
      },
      nudge: function () {
        var self = this;
        if (this.busy || this.readOnly || this.nudgeMinutes > 0) { return; }
        this.busy = true;
        this.error = "";
        call("POST", this.urls.nudge, this.csrf).then(function (reply) {
          self.busy = false;
          if (!reply.ok) { self.fail(reply); return; }
          self.setNudgeWait(reply.nudge_wait);
          self.done(reply);
        }).catch(function () {
          self.busy = false;
          self.error = "Could not send. Check your connection and try again.";
        });
      },
      prayed: function () {
        var self = this;
        if (this.busy || this.readOnly || this.prayedToday) { return; }
        this.busy = true;
        this.error = "";
        call("POST", this.urls.prayed, this.csrf).then(function (reply) {
          self.busy = false;
          if (!reply.ok) { self.fail(reply); return; }
          self.prayedToday = true;
          self.done(reply);
        }).catch(function () {
          self.busy = false;
          self.error = "Could not send. Check your connection and try again.";
        });
      }
    };
  };
})();
