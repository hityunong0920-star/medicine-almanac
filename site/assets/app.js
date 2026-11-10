/* 每日一药 · 交互：日期轮换、搜索筛选、收藏/已读、目录、字号、分享 */
(function () {
  "use strict";

  var data = window.__DAILY_DRUG__ || null;
  var root = document.documentElement;
  var WEEK_CHARS = "日一二三四五六";

  var FAV_KEY = "ma:favs";
  var READ_KEY = "ma:read";
  var STREAK_KEY = "ma:streak";
  var FONT_KEY = "ma:font";
  var PAGE_SIZE = 48;

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

  var STORE = {
    get: function (key, fallback) {
      try {
        var raw = localStorage.getItem(key);
        return raw ? JSON.parse(raw) : fallback;
      } catch (error) {
        return fallback;
      }
    },
    set: function (key, value) {
      try {
        localStorage.setItem(key, JSON.stringify(value));
      } catch (error) {
        /* 隐私模式忽略 */
      }
    }
  };

  function getFavs() {
    var value = STORE.get(FAV_KEY, []);
    return Object.prototype.toString.call(value) === "[object Array]" ? value : [];
  }

  function getRead() {
    var value = STORE.get(READ_KEY, {});
    return value && typeof value === "object" ? value : {};
  }

  function getStreak() {
    var value = STORE.get(STREAK_KEY, null);
    return value && typeof value === "object" ? value : { last: "", count: 0 };
  }

  function toast(message) {
    var el = document.getElementById("toast");
    if (!el) {
      el = document.createElement("div");
      el.id = "toast";
      el.className = "toast";
      el.setAttribute("role", "status");
      document.body.appendChild(el);
    }
    el.textContent = message;
    el.hidden = false;
    clearTimeout(el._timer);
    el._timer = setTimeout(function () {
      el.hidden = true;
    }, 2200);
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

  /* ------------------------------------------------------------ 阅读档案 */
  function renderProfile() {
    var box = document.getElementById("profile");
    if (!box || !data) return;

    var read = getRead();
    var favs = getFavs();
    var known = {};
    data.drugs.forEach(function (drug) {
      known[drug.id] = true;
    });

    var readCount = Object.keys(read).filter(function (id) {
      return known[id];
    }).length;
    var favCount = favs.filter(function (id) {
      return known[id];
    }).length;

    var today = shanghaiTodayISO();
    var yesterday = isoFromUTC(new Date(parseISO(today).getTime() - 86400000));
    var streak = getStreak();
    var alive = streak.last === today || streak.last === yesterday;
    var days = alive ? Math.max(1, streak.count || 0) : 0;

    box.hidden = false;
    setText(
      "profile-text",
      "已读 " +
        readCount +
        " / " +
        data.drugs.length +
        " 味 · 收藏 " +
        favCount +
        " 味 · 连续阅读 " +
        days +
        " 天"
    );
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
    var loadMore = document.getElementById("load-more");
    var favToggle = document.getElementById("fav-toggle");
    var active = "all";
    var shown = PAGE_SIZE;

    function syncCardState() {
      var favs = getFavs();
      var read = getRead();
      cards.forEach(function (card) {
        var id = card.getAttribute("data-id");
        card.classList.toggle("is-fav", favs.indexOf(id) !== -1);
        card.classList.toggle("is-read", !!read[id]);
      });
    }

    function syncChips() {
      if (!filters) return;
      Array.prototype.forEach.call(filters.querySelectorAll(".chip"), function (chip) {
        chip.classList.toggle(
          "is-active",
          active !== "__fav" && chip.getAttribute("data-filter") === active
        );
      });
    }

    function apply(reset) {
      if (reset) shown = PAGE_SIZE;
      var query = search ? search.value.trim().toLowerCase() : "";
      var filtering = active !== "all" || !!query;
      var matched = 0;
      var visible = 0;

      cards.forEach(function (card) {
        var ok;
        if (active === "__fav") {
          ok = card.classList.contains("is-fav");
        } else {
          ok = active === "all" || card.getAttribute("data-category") === active;
        }
        if (ok && query) {
          ok = (card.getAttribute("data-search") || "").indexOf(query) !== -1;
        }

        if (ok) matched += 1;
        var show = ok && (filtering || matched <= shown);
        card.hidden = !show;
        if (show) visible += 1;
      });

      if (result) {
        if (filtering) {
          result.textContent =
            active === "__fav" ? "收藏 " + matched + " 味" : "显示 " + matched + " 味";
        } else {
          result.textContent = "显示 " + visible + " / " + cards.length + " 味";
        }
      }

      if (empty) {
        empty.hidden = matched !== 0;
        empty.textContent =
          active === "__fav"
            ? "还没有收藏。打开一篇药，点标题下方的「收藏」。"
            : "没有找到这味药，换个关键词试试。";
      }

      if (loadMore) {
        loadMore.hidden = filtering || matched <= shown;
        if (!loadMore.hidden) {
          loadMore.textContent = "加载更多（还有 " + (matched - shown) + " 味）";
        }
      }

      if (favToggle) {
        favToggle.setAttribute("aria-pressed", active === "__fav" ? "true" : "false");
        favToggle.textContent = active === "__fav" ? "显示全部" : "只看收藏";
      }

      syncChips();
    }

    if (filters) {
      filters.addEventListener("click", function (event) {
        var button = event.target.closest("button[data-filter]");
        if (!button) return;
        active = button.getAttribute("data-filter");
        apply(true);
      });
    }

    if (search) {
      search.addEventListener("input", function () {
        apply(true);
      });

      document.addEventListener("keydown", function (event) {
        if (event.key !== "/" || event.metaKey || event.ctrlKey || event.altKey) return;
        var tag = (document.activeElement && document.activeElement.tagName) || "";
        if (tag === "INPUT" || tag === "TEXTAREA") return;
        event.preventDefault();
        search.focus();
      });
    }

    if (loadMore) {
      loadMore.addEventListener("click", function () {
        shown += PAGE_SIZE;
        apply(false);
      });
    }

    if (favToggle) {
      favToggle.addEventListener("click", function () {
        if (active !== "__fav" && getFavs().length === 0) {
          toast("还没有收藏，打开一篇药点「收藏」");
          return;
        }
        active = active === "__fav" ? "all" : "__fav";
        apply(true);
      });
    }

    syncCardState();
    apply(true);

    window.addEventListener("pageshow", function () {
      syncCardState();
      apply(false);
      renderProfile();
    });
  }

  /* ------------------------------------------------------------ 文章工具 */
  function setupToc() {
    var prose = document.querySelector(".prose");
    var list = document.getElementById("toc-list");
    var box = document.getElementById("toc");
    if (!prose || !list || !box) return;

    var headings = prose.querySelectorAll("h2");
    if (headings.length < 2) return;

    Array.prototype.forEach.call(headings, function (heading, index) {
      if (!heading.id) heading.id = "sec-" + (index + 1);
      var item = document.createElement("li");
      var link = document.createElement("a");
      link.href = "#" + heading.id;
      link.textContent = heading.textContent;
      item.appendChild(link);
      list.appendChild(item);
    });

    box.hidden = false;
  }

  function setupFont() {
    var size = parseFloat(STORE.get(FONT_KEY, 1.0625));
    if (!isFinite(size)) size = 1.0625;

    function apply() {
      root.style.setProperty("--reading-size", size.toFixed(3) + "rem");
    }
    apply();

    function bind(id, delta) {
      var button = document.getElementById(id);
      if (!button) return;
      button.addEventListener("click", function () {
        size = Math.min(1.35, Math.max(0.95, size + delta));
        STORE.set(FONT_KEY, size);
        apply();
      });
    }
    bind("font-up", 0.0625);
    bind("font-down", -0.0625);
  }

  function setupShare() {
    var button = document.getElementById("share-btn");
    if (!button) return;

    function fallbackCopy() {
      var area = document.createElement("textarea");
      area.value = location.href;
      area.setAttribute("readonly", "readonly");
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.appendChild(area);
      area.select();
      try {
        document.execCommand("copy");
        toast("链接已复制");
      } catch (error) {
        toast("复制失败，请手动复制地址栏");
      }
      document.body.removeChild(area);
    }

    button.addEventListener("click", function () {
      var payload = { title: document.title, url: location.href };
      if (navigator.share) {
        navigator.share(payload).catch(function () {});
        return;
      }
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(location.href).then(
          function () {
            toast("链接已复制");
          },
          fallbackCopy
        );
        return;
      }
      fallbackCopy();
    });
  }

  function markRead(id) {
    var today = shanghaiTodayISO();
    var read = getRead();
    read[id] = today;
    STORE.set(READ_KEY, read);

    var streak = getStreak();
    if (streak.last === today) return;
    var yesterday = isoFromUTC(new Date(parseISO(today).getTime() - 86400000));
    STORE.set(STREAK_KEY, {
      last: today,
      count: streak.last === yesterday ? Math.max(1, streak.count || 0) + 1 : 1
    });
  }

  function setupFavourite(id) {
    var button = document.getElementById("fav-btn");
    var icon = document.getElementById("fav-icon");
    var favs = getFavs();

    function paint() {
      var on = favs.indexOf(id) !== -1;
      if (button) {
        button.classList.toggle("is-active", on);
        button.setAttribute("aria-pressed", on ? "true" : "false");
      }
      if (icon) icon.textContent = on ? "★" : "☆";
      setText("fav-label", on ? "已收藏" : "收藏");
    }

    paint();
    if (!button) return;
    button.addEventListener("click", function () {
      var index = favs.indexOf(id);
      if (index === -1) {
        favs.push(id);
        toast("已加入收藏");
      } else {
        favs.splice(index, 1);
        toast("已取消收藏");
      }
      STORE.set(FAV_KEY, favs);
      paint();
    });
  }

  function setupArticle() {
    var id = window.__DRUG_ID__;
    if (!id) return;

    markRead(id);
    setupFavourite(id);
    setupToc();
    setupFont();
    setupShare();
    setupProgress();
    markToday();
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
  renderProfile();
  setupLibrary();
  setupArticle();
})();
