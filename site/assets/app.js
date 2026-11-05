/* 每日一药 · 交互：日期轮换、搜索筛选、深浅色、阅读进度 */
(function () {
  "use strict";

  var data = window.__DAILY_DRUG__ || null;
  var root = document.documentElement;
  var WEEK_CHARS = "日一二三四五六";

  /* ---------------------------------------------------------------- 工具 */
  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function pad2(n) {
    return n < 10 ? "0" + n : "" + n;
  }

  function parseISO(iso) {
    var parts = iso.split("-").map(Number);
    return new Date(Date.UTC(parts[0], parts[1] - 1, parts[2]));
  }

  function isoFromUTC(dateObj) {
    return (
      dateObj.getUTCFullYear() +
      "-" +
      pad2(dateObj.getUTCMonth() + 1) +
      "-" +
      pad2(dateObj.getUTCDate())
    );
  }

  /* 以北京时间为准，保证同一天全球看到同一味药 */
  function shanghaiTodayISO() {
    try {
      var fmt = new Intl.DateTimeFormat("en-CA", {
        timeZone: "Asia/Shanghai",
        year: "numeric",
        month: "2-digit",
        day: "2-digit"
      });
      var text = fmt.format(new Date());
      if (/^\d{4}-\d{2}-\d{2}$/.test(text)) return text;
    } catch (error) {
      /* 老浏览器回退到 UTC+8 */
    }
    return isoFromUTC(new Date(Date.now() + 8 * 3600 * 1000));
  }

  function dayNumber(iso) {
    return Math.round((parseISO(iso) - parseISO(data.start)) / 86400000);
  }

  function indexFor(iso) {
    var total = data.drugs.length;
    return ((dayNumber(iso) % total) + total) % total;
  }

  function setText(id, text) {
    var el = document.getElementById(id);
    if (el) el.textContent = text;
  }

  /* ---------------------------------------------------------------- 主题 */
  var toggle = document.getElementById("theme-toggle");

  function syncTheme() {
    var dark = root.dataset.theme === "dark";
    if (toggle) {
      toggle.setAttribute("aria-label", dark ? "切换到浅色模式" : "切换到深色模式");
    }
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute("content", dark ? "#14110e" : "#f6f1e6");
  }

  if (toggle) {
    toggle.addEventListener("click", function () {
      root.dataset.theme = root.dataset.theme === "dark" ? "light" : "dark";
      try {
        localStorage.setItem("dd-theme", root.dataset.theme);
      } catch (error) {
        /* 隐私模式忽略 */
      }
      syncTheme();
    });
  }
  syncTheme();

  /* ---------------------------------------------------------------- 今日 */
  function renderToday() {
    if (!data || !document.getElementById("today-name")) return null;

    var iso = shanghaiTodayISO();
    var drug = data.drugs[indexFor(iso)];
    var dateObj = parseISO(iso);
    var issue = Math.max(1, dayNumber(iso) + 1);

    setText(
      "today-date",
      dateObj.getUTCFullYear() +
        "年" +
        (dateObj.getUTCMonth() + 1) +
        "月" +
        dateObj.getUTCDate() +
        "日"
    );
    setText("today-weekday", WEEK_CHARS.charAt(dateObj.getUTCDay()));
    setText("today-issue", String(issue));
    setText("today-name", drug.name);
    setText("today-en", drug.en);
    setText("today-summary", drug.summary);
    setText("today-ghost", pad2(drug.no));

    var chips = document.getElementById("today-chips");
    if (chips) {
      var tags = [drug.category].concat(drug.tags.slice(0, 2));
      chips.innerHTML = tags
        .map(function (tag) {
          return '<span class="tag tag--hero">' + esc(tag) + "</span>";
        })
        .join("");
    }

    var link = document.getElementById("today-link");
    if (link) link.setAttribute("href", "drug/" + drug.id + ".html");

    var source = document.getElementById("today-source");
    if (source) {
      source.hidden = drug.kind !== "auto";
      source.textContent = drug.brief
        ? "简卡 · 自动汇编自维基百科"
        : "资料卡 · 自动汇编自维基百科";
    }

    document.title = drug.name + " · 每日一药";
    return drug;
  }

  /* ---------------------------------------------------------------- 排期 */
  function renderWeek() {
    var list = document.getElementById("week-list");
    if (!data || !list) return;

    var todayISO = shanghaiTodayISO();
    var start = parseISO(todayISO);
    var html = "";

    for (var i = 0; i < 7; i += 1) {
      var dateObj = new Date(start.getTime() + i * 86400000);
      var iso = isoFromUTC(dateObj);
      var drug = data.drugs[indexFor(iso)];
      html +=
        '<a class="day' +
        (i === 0 ? " is-today" : "") +
        '" href="drug/' +
        esc(drug.id) +
        '.html">' +
        '<span class="day__date">' +
        pad2(dateObj.getUTCMonth() + 1) +
        "." +
        pad2(dateObj.getUTCDate()) +
        "</span>" +
        '<span class="day__week">星期' +
        WEEK_CHARS.charAt(dateObj.getUTCDay()) +
        "</span>" +
        '<span class="day__name">' +
        esc(drug.name) +
        "</span>" +
        '<span class="day__cat">' +
        esc(drug.category) +
        "</span>" +
        "</a>";
    }

    list.innerHTML = html;
  }

  /* ---------------------------------------------------------------- 药库 */
  function setupLibrary() {
    var grid = document.getElementById("grid");
    if (!grid) return;

    var cards = Array.prototype.slice.call(grid.querySelectorAll(".card"));
    var search = document.getElementById("search");
    var result = document.getElementById("result");
    var empty = document.getElementById("empty");
    var filters = document.getElementById("filters");
    var active = "all";

    function apply() {
      var query = search ? search.value.trim().toLowerCase() : "";
      var shown = 0;

      cards.forEach(function (card) {
        var category = card.getAttribute("data-category");
        var haystack = card.getAttribute("data-search") || "";
        var visible =
          (active === "all" || category === active) &&
          (!query || haystack.indexOf(query) !== -1);

        card.hidden = !visible;
        if (visible) shown += 1;
      });

      if (result) result.textContent = "显示 " + shown + " 味";
      if (empty) empty.hidden = shown !== 0;
    }

    if (filters) {
      filters.addEventListener("click", function (event) {
        var button = event.target.closest("button[data-filter]");
        if (!button) return;

        active = button.getAttribute("data-filter");
        Array.prototype.forEach.call(
          filters.querySelectorAll(".chip"),
          function (chip) {
            chip.classList.toggle("is-active", chip === button);
          }
        );
        apply();
      });
    }

    if (search) {
      search.addEventListener("input", apply);

      document.addEventListener("keydown", function (event) {
        if (event.key !== "/" || event.metaKey || event.ctrlKey || event.altKey) return;
        var tag = (document.activeElement && document.activeElement.tagName) || "";
        if (tag === "INPUT" || tag === "TEXTAREA") return;
        event.preventDefault();
        search.focus();
      });
    }

    var randomBtn = document.getElementById("random-btn");
    if (randomBtn) {
      randomBtn.addEventListener("click", function () {
        var pool = cards.filter(function (card) {
          return !card.hidden;
        });
        if (!pool.length) pool = cards;
        var pick = pool[Math.floor(Math.random() * pool.length)];
        window.location.href = pick.getAttribute("href");
      });
    }

    apply();
  }

  /* ------------------------------------------------------------ 阅读进度 */
  function setupProgress() {
    var bar = document.getElementById("progress");
    if (!bar) return;

    var ticking = false;

    function update() {
      var doc = document.documentElement;
      var max = doc.scrollHeight - doc.clientHeight;
      var ratio = max > 0 ? Math.min(1, doc.scrollTop / max) : 0;
      bar.style.width = ratio * 100 + "%";
      ticking = false;
    }

    window.addEventListener(
      "scroll",
      function () {
        if (!ticking) {
          ticking = true;
          window.requestAnimationFrame(update);
        }
      },
      { passive: true }
    );

    update();
  }

  /* ------------------------------------------------------- 文章页“今日”标 */
  function markToday() {
    var badge = document.getElementById("today-badge");
    if (!badge || !data || !window.__DRUG_ID__) return;

    var todayDrug = data.drugs[indexFor(shanghaiTodayISO())];
    if (todayDrug.id === window.__DRUG_ID__) {
      badge.hidden = false;
    }
  }

  renderToday();
  renderWeek();
  setupLibrary();
  setupProgress();
  markToday();
})();
