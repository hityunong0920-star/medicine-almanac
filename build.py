#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每日一药 · 静态站点生成器
=========================

零第三方依赖，Python 3.9+ 即可运行。

    python build.py      # 生成 dist/

内容放在 content/drugs/*.md，使用简单的 front matter + Markdown。
"""

from __future__ import annotations

import html
import json
import re
import shutil
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent
CONTENT_DIRS = (ROOT / "content" / "drugs", ROOT / "content" / "auto")
ASSETS_DIR = ROOT / "site" / "assets"
DIST_DIR = ROOT / "dist"

# ---------------------------------------------------------------------------
# 站点配置：一般只需要改这里
# ---------------------------------------------------------------------------
SITE_NAME = "每日一药"
SITE_TAGLINE = "一天认识一味药"
SITE_DESC = "每天介绍一种药：它的起源、经典故事、作用机理与安全边界。"
REPO_URL = "https://github.com/hityunong0920-star/medicine-almanac"  # 顶栏 GitHub 链接
START_DATE = date(2026, 11, 5)      # 第 1 期从这天开始
TZ = timezone(timedelta(hours=8))   # 北京时间（无夏令时）

CATEGORY_ORDER = [
    "抗感染",
    "心血管",
    "内分泌",
    "神经精神",
    "镇痛麻醉",
    "抗肿瘤",
    "免疫风湿",
    "呼吸消化",
    "血液泌尿",
    "营养维生素",
    "皮肤五官",
    "其他",
]
REQUIRED_FIELDS = ("id", "name", "en", "category", "summary")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
CJK_RE = re.compile(r"[\u3400-\u9fff]")
WEEKDAYS = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def cycle_index(day: date, total: int) -> int:
    """返回某一天对应的药品下标（按天数轮换）。"""
    delta = (day - START_DATE).days
    return ((delta % total) + total) % total


def issue_number(day: date) -> int:
    return max(1, (day - START_DATE).days + 1)


def join_lines(parts: list[str]) -> str:
    """把折行合并成段落：中文之间不补空格，西文之间补一个空格。"""
    if not parts:
        return ""
    out = parts[0]
    for part in parts[1:]:
        if out and part and CJK_RE.match(out[-1]) and CJK_RE.match(part[0]):
            out += part
        else:
            out += " " + part
    return out


# ---------------------------------------------------------------------------
# Markdown（只实现本站用到的子集，够用且可预测）
# ---------------------------------------------------------------------------
def inline(text: str) -> str:
    text = html.escape(text, quote=False)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", r'<a href="\2" rel="noopener">\1</a>', text)
    return text


def md_to_html(md: str) -> str:
    lines = md.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    i, total = 0, len(lines)

    def is_block_start(line: str) -> bool:
        s = line.strip()
        return bool(
            re.match(r"^#{2,3}\s", s)
            or s.startswith(("!>", ">"))
            or re.match(r"^[-*]\s+", s)
            or re.match(r"^\d+\.\s+", s)
        )

    while i < total:
        raw = lines[i]
        line = raw.strip()
        if not line:
            i += 1
            continue

        if line.startswith("### "):
            out.append(f"<h3>{inline(line[4:])}</h3>")
            i += 1
            continue

        if line.startswith("## "):
            out.append(f"<h2>{inline(line[3:])}</h2>")
            i += 1
            continue

        if line.startswith("!>"):
            buf: list[str] = []
            while i < total and lines[i].strip().startswith("!>"):
                buf.append(lines[i].strip()[2:].strip())
                i += 1
            out.append(f'<aside class="note"><p>{inline(join_lines(buf))}</p></aside>')
            continue

        if line.startswith(">"):
            buf = []
            while i < total and lines[i].strip().startswith(">"):
                buf.append(lines[i].strip().lstrip(">").strip())
                i += 1
            out.append(f"<blockquote><p>{inline(join_lines(buf))}</p></blockquote>")
            continue

        if re.match(r"^[-*]\s+", line):
            items: list[str] = []
            while i < total and re.match(r"^[-*]\s+", lines[i].strip()):
                items.append(re.sub(r"^[-*]\s+", "", lines[i].strip()))
                i += 1
            body = "".join(f"<li>{inline(item)}</li>" for item in items)
            out.append(f"<ul>{body}</ul>")
            continue

        if re.match(r"^\d+\.\s+", line):
            items = []
            while i < total and re.match(r"^\d+\.\s+", lines[i].strip()):
                items.append(re.sub(r"^\d+\.\s+", "", lines[i].strip()))
                i += 1
            body = "".join(f"<li>{inline(item)}</li>" for item in items)
            out.append(f"<ol>{body}</ol>")
            continue

        buf = [line]
        i += 1
        while i < total and lines[i].strip() and not is_block_start(lines[i]):
            buf.append(lines[i].strip())
            i += 1
        out.append(f"<p>{inline(join_lines(buf))}</p>")

    return "\n".join(out)


# ---------------------------------------------------------------------------
# 读取内容
# ---------------------------------------------------------------------------
def parse_front_matter(text: str) -> tuple[dict, str]:
    text = text.replace("\r\n", "\n").lstrip("\ufeff")
    if not text.startswith("---"):
        raise ValueError("缺少以 --- 开头的 front matter")
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise ValueError("front matter 没有闭合（需要第二个 ---）")

    meta: dict = {}
    for raw in parts[1].splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition(":")
        if not sep:
            raise ValueError(f"无法解析这一行 front matter：{raw!r}")
        key, value = key.strip(), value.strip()
        if value.startswith("[") and value.endswith("]"):
            items = [v.strip().strip("'\"") for v in value[1:-1].split(",")]
            meta[key] = [v for v in items if v]
        else:
            meta[key] = value.strip("'\"")
    return meta, parts[2]


def load_drugs() -> list[dict]:
    files: list[Path] = []
    for directory in CONTENT_DIRS:
        if directory.exists():
            files.extend(sorted(p for p in directory.glob("*.md") if not p.name.startswith("_")))

    if not files:
        raise SystemExit("没有找到任何 .md 内容（content/drugs 或 content/auto）")

    drugs: list[dict] = []
    curated_ids: set[str] = set()
    curated_names: set[str] = set()

    for path in files:
        try:
            meta, body = parse_front_matter(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise SystemExit(f"{path.name}: {exc}") from exc

        missing = [f for f in REQUIRED_FIELDS if not meta.get(f)]
        if missing:
            raise SystemExit(f"{path.name}: 缺少字段 {', '.join(missing)}")
        if not ID_RE.match(str(meta["id"])):
            raise SystemExit(f"{path.name}: id 只能用小写字母、数字和连字符，例如 aspirin")

        kind = str(meta.get("kind", "curated"))
        name_key = re.sub(r"\s+", "", str(meta["name"]))
        if kind == "auto" and (str(meta["id"]) in curated_ids or name_key in curated_names):
            continue  # 自动资料卡与手写深读重复时，以手写稿为准

        year_text = str(meta.get("year", "")).strip()
        meta["year"] = int(year_text) if year_text.isdigit() else 0
        meta["alias"] = meta.get("alias", "")
        meta["family"] = meta.get("family", "")
        meta["kind"] = kind
        meta["brief"] = str(meta.get("brief", "")).lower() == "true"
        meta["source_title"] = meta.get("source_title", "")
        meta["source_url"] = meta.get("source_url", "")
        meta["updated"] = meta.get("updated", "")
        tags = meta.get("tags") or []
        meta["tags"] = tags if isinstance(tags, list) else [tags]
        meta["summary"] = str(meta["summary"])
        meta["body"] = body.strip()
        meta["html"] = md_to_html(meta["body"])
        meta["source"] = path.name

        if kind == "curated":
            curated_ids.add(str(meta["id"]))
            curated_names.add(name_key)
        drugs.append(meta)

    ids = [d["id"] for d in drugs]
    duplicated = {i for i in ids if ids.count(i) > 1}
    if duplicated:
        raise SystemExit(f"id 重复：{', '.join(sorted(duplicated))}")

    ordered = arrange_drugs(drugs)
    for index, drug in enumerate(ordered, start=1):
        drug["no"] = index
    return ordered


def arrange_drugs(drugs: list[dict]) -> list[dict]:
    """按类别均匀交错：大类别出现得更频繁，但任何阶段都不会只剩一类。"""
    groups: dict[str, list[dict]] = {}
    for drug in drugs:
        groups.setdefault(drug["category"], []).append(drug)
    for items in groups.values():
        items.sort(key=lambda d: (d["year"] or 9999, d["name"]))

    keyed: list[tuple[float, str, dict]] = []
    for category, items in groups.items():
        count = len(items)
        for index, drug in enumerate(items):
            # 把每一类均匀铺在 [0, 1) 上，再统一排序
            keyed.append(((index + 0.5) / count, category, drug))
    keyed.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in keyed]


# ---------------------------------------------------------------------------
# 页面骨架
# ---------------------------------------------------------------------------
def shell(
    *,
    title: str,
    description: str,
    content: str,
    prefix: str,
    body_class: str,
    data_script: str = "",
) -> str:
    github = (
        f'<a class="topnav__link" href="{esc(REPO_URL)}" target="_blank" rel="noopener">GitHub</a>'
        if REPO_URL
        else ""
    )
    return f"""<!doctype html>
<html lang="zh-CN" data-theme="light">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="{esc(description)}">
<meta name="color-scheme" content="light dark">
<meta name="theme-color" content="#f6f1e6">
<meta property="og:type" content="website">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(description)}">
<link rel="icon" href="{prefix}assets/favicon.svg" type="image/svg+xml">
<link rel="stylesheet" href="{prefix}assets/style.css">
<script src="{prefix}assets/theme.js"></script>
</head>
<body class="{body_class}">
<a class="skip-link" href="#main">跳到主要内容</a>
<header class="topbar">
  <div class="wrap topbar__inner">
    <a class="brand" href="{prefix}index.html" aria-label="{SITE_NAME} 首页">
      <span class="brand__seal" aria-hidden="true">药</span>
      <span class="brand__name">{SITE_NAME}<span class="brand__en">an almanac of medicine</span></span>
    </a>
    <nav class="topnav" aria-label="主导航">
      <a class="topnav__link" href="{prefix}index.html#today">今日</a>
      <a class="topnav__link" href="{prefix}index.html#week">排期</a>
      <a class="topnav__link" href="{prefix}index.html#library">药库</a>
      <a class="topnav__link" href="{prefix}index.html#about">关于</a>
      {github}
    </nav>
    <button class="theme-toggle" id="theme-toggle" type="button" aria-label="切换深浅色">
      <svg class="theme-toggle__icon theme-toggle__icon--moon" viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" focusable="false"><path d="M20.5 14.2A8.6 8.6 0 0 1 9.8 3.5a8.6 8.6 0 1 0 10.7 10.7Z" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/></svg>
      <svg class="theme-toggle__icon theme-toggle__icon--sun" viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" focusable="false"><circle cx="12" cy="12" r="4.1" fill="none" stroke="currentColor" stroke-width="1.7"/><path d="M12 2.4v2.2M12 19.4v2.2M2.4 12h2.2M19.4 12h2.2M5.2 5.2l1.6 1.6M17.2 17.2l1.6 1.6M18.8 5.2l-1.6 1.6M6.8 17.2l-1.6 1.6" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>
    </button>
  </div>
</header>
{content}
<footer class="footer">
  <div class="wrap footer__inner">
    <div class="footer__brand">
      <span class="brand__seal brand__seal--sm" aria-hidden="true">药</span>
      <div>
        <p class="footer__name">{SITE_NAME}</p>
        <p class="footer__tag">{SITE_TAGLINE}</p>
      </div>
    </div>
    <p class="footer__legal">内容仅供科普与学习，不构成医疗建议；处方药请遵医嘱。<br>代码 MIT · 深读文字 CC BY-NC-SA 4.0 · 资料卡 CC BY-SA 4.0</p>
  </div>
</footer>
{data_script}
<script src="{prefix}assets/app.js" defer></script>
</body>
</html>
"""


def card_html(drug: dict) -> str:
    tags = "".join(f'<span class="tag">{esc(t)}</span>' for t in drug["tags"][:3])
    tags_block = f'<span class="card__tags">{tags}</span>' if tags else ""
    blob = " ".join(
        [
            drug["name"],
            drug["en"],
            drug["alias"],
            drug["category"],
            drug["family"],
            drug["summary"],
            " ".join(drug["tags"]),
        ]
    ).lower()
    return f"""      <a class="card" href="drug/{esc(drug['id'])}.html" data-category="{esc(drug['category'])}" data-search="{esc(blob)}">
        <span class="card__top"><span class="card__no">№ {drug['no']:02d}</span><span class="card__cat">{esc(drug['category'])}</span></span>
        <span class="card__name">{esc(drug['name'])}</span>
        <span class="card__en">{esc(drug['en'])}</span>
        <span class="card__summary">{esc(drug['summary'])}</span>
        {tags_block}
      </a>"""


def build_index(drugs: list[dict], today: date, payload_script: str) -> str:
    total = len(drugs)
    hero = drugs[cycle_index(today, total)]
    issue = issue_number(today)
    hero_tags = "".join(
        f'<span class="tag tag--hero">{esc(t)}</span>' for t in [hero["category"], *hero["tags"][:2]]
    )

    week_items = []
    for offset in range(7):
        day = today + timedelta(days=offset)
        drug = drugs[cycle_index(day, total)]
        classes = "day is-today" if offset == 0 else "day"
        week_items.append(
            f"""        <a class="{classes}" href="drug/{esc(drug['id'])}.html">
          <span class="day__date">{day.month:02d}.{day.day:02d}</span>
          <span class="day__week">{WEEKDAYS[day.weekday()]}</span>
          <span class="day__name">{esc(drug['name'])}</span>
          <span class="day__cat">{esc(drug['category'])}</span>
        </a>"""
        )

    counts: dict[str, int] = {}
    for drug in drugs:
        counts[drug["category"]] = counts.get(drug["category"], 0) + 1
    ordered_cats = [c for c in CATEGORY_ORDER if c in counts]
    ordered_cats += [c for c in counts if c not in CATEGORY_ORDER]

    deep_count = sum(1 for drug in drugs if drug["kind"] == "curated")
    auto_count = total - deep_count

    chips = [
        f'<button class="chip is-active" type="button" data-filter="all">全部<span class="chip__count">{total}</span></button>'
    ]
    for cat in ordered_cats:
        chips.append(
            f'<button class="chip" type="button" data-filter="{esc(cat)}">{esc(cat)}<span class="chip__count">{counts[cat]}</span></button>'
        )

    cards = "\n".join(card_html(drug) for drug in drugs)
    hero_source_hidden = "" if hero["kind"] == "auto" else " hidden"
    hero_source_text = "简卡 · 自动汇编自维基百科" if hero.get("brief") else "资料卡 · 自动汇编自维基百科"

    content = f"""<main id="main">
  <div class="wrap">
    <section class="hero" id="today" aria-labelledby="today-name">
      <div class="hero__inner">
        <p class="hero__kicker"><span class="hero__meta"><span id="today-date">{today.year}年{today.month}月{today.day}日</span><span class="dot">·</span><span>星期<span id="today-weekday">{WEEKDAYS[today.weekday()][2:]}</span></span></span><span class="dot">·</span><span class="hero__issue">第 <span id="today-issue">{issue}</span> 期</span></p>
        <p class="hero__eyebrow"><span class="hero__pulse" aria-hidden="true"></span>今日一药</p>
        <h1 class="hero__title" id="today-name">{esc(hero['name'])}</h1>
        <p class="hero__en" id="today-en">{esc(hero['en'])}</p>
        <p class="hero__summary" id="today-summary">{esc(hero['summary'])}</p>
        <p class="hero__source" id="today-source"{hero_source_hidden}>{hero_source_text}</p>
        <div class="hero__chips" id="today-chips">{hero_tags}</div>
        <div class="hero__actions">
          <a class="btn btn--primary" id="today-link" href="drug/{esc(hero['id'])}.html">读这一味 <span aria-hidden="true">→</span></a>
          <button class="btn btn--ghost" id="random-btn" type="button">随机一味</button>
        </div>
      </div>
      <span class="hero__seal" aria-hidden="true">药</span>
      <span class="hero__ghost" id="today-ghost" aria-hidden="true">{hero['no']:02d}</span>
    </section>

    <section class="week" id="week" aria-labelledby="week-title">
      <div class="section-head">
        <h2 class="section-title" id="week-title">未来七日</h2>
        <p class="section-sub">按类别轮转 · 周期 {total} 天</p>
      </div>
      <div class="week__list" id="week-list">
{chr(10).join(week_items)}
      </div>
    </section>

    <section class="about" id="about" aria-labelledby="about-title">
      <div class="about__text">
        <h2 id="about-title">关于这个项目</h2>
        <p>「{SITE_NAME}」每天从药库里取一味药，写清楚它从哪里来、在体内如何工作，以及它的边界在哪里。药库分两层：<strong>{deep_count} 篇手写「深读」</strong>，加上 <strong>{auto_count} 张自动汇编的「资料卡」</strong>，合计 {total} 味，够一年不重样。</p>
        <p>深读按起源、经典故事、机理、今日之用、安全边界、冷知识展开；资料卡则从中文维基百科抓取历史、机理、用途与不良反应，保留原文链接，并随 GitHub Actions 每天刷新当周条目。资料不足的会标成「简卡」。轮换按类别交错，连续几天不会都是同一类药。</p>
        <p class="about__disclaimer">本站内容仅供科普与学习，不构成任何医疗建议。处方药请在医生或药师指导下使用；出现急症请立即就医。</p>
      </div>
      <aside class="about__card">
        <p class="about__card-title">它如何轮换</p>
        <p>药库按类别交错排列，每天取下一味；轮完一遍，再从头开始。</p>
        <p class="formula">(今天 − {START_DATE.isoformat()}) mod {total}</p>
        <p>当前药库 {total} 味，一个周期 {total} 天。新增药品后，周期自动变长。</p>
      </aside>
    </section>

    <section class="library" id="library" aria-labelledby="library-title">
      <div class="section-head">
        <h2 class="section-title" id="library-title">药库</h2>
        <p class="section-sub">共 <span id="drug-total">{total}</span> 味 · 每日联网更新</p>
      </div>
      <div class="toolbar">
        <div class="chips" id="filters" role="group" aria-label="按类别筛选">
{chr(10).join("          " + c for c in chips)}
        </div>
        <div class="search">
          <input id="search" type="search" placeholder="搜索药名、别名、机理关键词…" aria-label="搜索药库" autocomplete="off">
          <kbd>/</kbd>
        </div>
      </div>
      <p class="result" id="result" aria-live="polite">显示 {total} 味</p>
      <div class="grid" id="grid">
{cards}
      </div>
      <p class="empty" id="empty" hidden>没有找到这味药，换个关键词试试。</p>
    </section>
  </div>
</main>
"""
    return shell(
        title=f"{SITE_NAME} · {SITE_TAGLINE}",
        description=SITE_DESC,
        content=content,
        prefix="",
        body_class="page-home",
        data_script=payload_script,
    )


def build_drug(drug: dict, drugs: list[dict], payload_script: str) -> str:
    total = len(drugs)
    prev_drug = drugs[(drug["no"] - 2) % total]
    next_drug = drugs[drug["no"] % total]
    minutes = max(2, round(len(re.sub(r"\s+", "", drug["body"])) / 320))
    alias_fact = (
        f'<div><dt>别名</dt><dd>{esc(drug["alias"])}</dd></div>' if drug["alias"] else ""
    )
    year_fact = (
        f'<div><dt>初登场</dt><dd>{drug["year"]}</dd></div>' if drug["year"] else ""
    )
    family_fact = (
        f'<div><dt>类别</dt><dd>{esc(drug["family"])}</dd></div>' if drug["family"] else ""
    )
    tags = "".join(f'<span class="tag">{esc(t)}</span>' for t in drug["tags"])
    tags_block = f'<div class="article__tags">{tags}</div>' if tags else ""

    if drug["kind"] == "auto":
        label = "简卡 · 自动汇编" if drug["brief"] else "资料卡 · 自动汇编"
        kind_badge = f'<p class="article__kind">{label}</p>'
        updated = f"，最后更新于 {esc(drug['updated'])}" if drug["updated"] else ""
        source_title = esc(drug["source_title"])
        if drug["source_url"]:
            source_title = (
                f'<a href="{esc(drug["source_url"])}" target="_blank" rel="noopener">{source_title}</a>'
            )
        source_block = f"""      <aside class="source">
        <p class="source__label">资料来源</p>
        <p>本文由中文维基百科条目《{source_title}》自动汇编，遵循 <a href="https://creativecommons.org/licenses/by-sa/4.0/deed.zh" target="_blank" rel="noopener">CC BY-SA 4.0</a> 许可{updated}。</p>
        <p class="source__note">自动汇编内容可能存在滞后或错误，请以药品说明书与医生意见为准。</p>
      </aside>
"""
    else:
        kind_badge = ""
        source_block = ""

    content = f"""<div class="progress" id="progress" aria-hidden="true"></div>
<main id="main">
  <div class="wrap">
    <article class="article">
      <header class="article__head">
        <p class="article__kicker"><a href="../index.html#library">药库</a><span class="dot">/</span>№ {drug['no']:02d} / 共 {total} 味<span class="dot">/</span>{esc(drug['category'])}</p>
        <p class="article__badge" id="today-badge" hidden>今日一药</p>
        {kind_badge}
        <h1 class="article__title">{esc(drug['name'])}</h1>
        <p class="article__en">{esc(drug['en'])}</p>
        <p class="article__summary">{esc(drug['summary'])}</p>
        <dl class="facts">
          {year_fact}
          {family_fact}
          {alias_fact}
          <div><dt>阅读</dt><dd>约 {minutes} 分钟</dd></div>
        </dl>
        {tags_block}
      </header>
      <div class="prose">
{drug['html']}
      </div>
{source_block}      <nav class="article__nav" aria-label="上一篇 / 下一篇">
        <a class="navcard" href="{esc(prev_drug['id'])}.html"><span class="navcard__label">← 上一味</span><span class="navcard__name">{esc(prev_drug['name'])}</span></a>
        <a class="navcard navcard--next" href="{esc(next_drug['id'])}.html"><span class="navcard__label">下一味 →</span><span class="navcard__name">{esc(next_drug['name'])}</span></a>
      </nav>
      <p class="article__back"><a href="../index.html">回到今天的药</a></p>
    </article>
  </div>
</main>
"""
    data_script = (
        f'<script>window.__DRUG_ID__ = "{esc(drug["id"])}";</script>\n' + payload_script
    )
    return shell(
        title=f"{drug['name']} · {SITE_NAME}",
        description=drug["summary"],
        content=content,
        prefix="../",
        body_class="page-drug",
        data_script=data_script,
    )


def build_404() -> str:
    content = f"""<main id="main">
  <div class="wrap">
    <section class="notfound">
      <p class="notfound__code">404</p>
      <h1 class="notfound__title">这味药不在药库里</h1>
      <p class="notfound__text">页面可能已经移动，或者链接输错了。回到首页，看看今天轮到哪一味。</p>
      <a class="btn btn--primary" href="./index.html">回到今日 <span aria-hidden="true">→</span></a>
    </section>
  </div>
</main>
"""
    return shell(
        title=f"页面未找到 · {SITE_NAME}",
        description="页面未找到。",
        content=content,
        prefix="",
        body_class="page-404",
    )


# ---------------------------------------------------------------------------
# 构建
# ---------------------------------------------------------------------------
def main() -> None:
    drugs = load_drugs()
    today = datetime.now(TZ).date()
    total = len(drugs)

    payload = {
        "start": START_DATE.isoformat(),
        "drugs": [
            {
                "id": d["id"],
                "name": d["name"],
                "en": d["en"],
                "alias": d["alias"],
                "category": d["category"],
                "family": d["family"],
                "year": d["year"],
                "tags": d["tags"],
                "summary": d["summary"],
                "no": d["no"],
                "kind": d["kind"],
                "brief": d["brief"],
            }
            for d in drugs
        ],
    }
    payload_script = (
        "<script>window.__DAILY_DRUG__ = "
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
        + ";</script>"
    )

    if DIST_DIR.exists():
        shutil.rmtree(DIST_DIR)
    DIST_DIR.mkdir(parents=True)

    assets_out = DIST_DIR / "assets"
    shutil.copytree(ASSETS_DIR, assets_out)
    (DIST_DIR / ".nojekyll").write_text("", encoding="utf-8")

    (DIST_DIR / "index.html").write_text(
        build_index(drugs, today, payload_script), encoding="utf-8"
    )
    (DIST_DIR / "404.html").write_text(build_404(), encoding="utf-8")

    drug_dir = DIST_DIR / "drug"
    drug_dir.mkdir()
    for drug in drugs:
        (drug_dir / f"{drug['id']}.html").write_text(
            build_drug(drug, drugs, payload_script), encoding="utf-8"
        )

    hero = drugs[cycle_index(today, total)]
    deep_count = sum(1 for drug in drugs if drug["kind"] == "curated")
    print(f"[OK] 已生成 {total} 味 -> {DIST_DIR}")
    print(f"     深读 {deep_count} 篇 · 资料卡 {total - deep_count} 张")
    print(f"     今日 {today} 第 {issue_number(today)} 期：{hero['name']}")
    print("     本地预览：python -m http.server 8000 --directory dist")


if __name__ == "__main__":
    main()
