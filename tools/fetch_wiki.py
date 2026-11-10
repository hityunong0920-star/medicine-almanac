#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从中文维基百科批量抓取药品资料，生成 content/auto/*.md 资料卡。

用法：
    python tools/fetch_wiki.py --all             # 按种子表批量生成
    python tools/fetch_wiki.py --all --limit 20  # 先试 20 条
    python tools/fetch_wiki.py --upcoming 7      # 刷新未来 7 天要登场的资料卡
    python tools/fetch_wiki.py --ids quinine     # 刷新指定条目

原理：
- 用英文维基 langlinks 批量解析中文标题；
- 用 Special:Export 一次导出 40 个条目的 wikitext（请求数从 500+ 降到 16 次）；
- 本地剥模板/表格/引用，按二级标题切分；
- 用 OpenCC 的 TSCharacters 表把繁体转成简体；
- 只保留具备“用途/机理”和“安全”章节的条目。
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import html as html_module
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from seeds import SEEDS  # noqa: E402

AUTO_DIR = ROOT / "content" / "auto"
CURATED_DIR = ROOT / "content" / "drugs"
CACHE_DIR = ROOT / "data" / "cache"
T2S_FILE = Path(__file__).with_name("ts_characters.txt")
UA = "MedAlmanacBot/0.4 (educational static site; low-volume; contact via GitHub)"
TZ = timezone(timedelta(hours=8))
TODAY = datetime.now(TZ).date()
BATCH_SIZE = 40

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


# ---------------------------------------------------------------------------
# 网络
# ---------------------------------------------------------------------------
def http_text(url: str) -> str:
    last: Exception | None = None
    for attempt in range(6):
        try:
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": UA,
                    "Accept": "text/html,application/xhtml+xml,application/xml",
                    "Accept-Encoding": "gzip",
                },
            )
            with urllib.request.urlopen(request, timeout=90) as response:
                raw = response.read()
                if response.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return raw.decode("utf-8", "ignore")
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code == 429:
                time.sleep(max(10, int(exc.headers.get("Retry-After", "10"))))
            else:
                time.sleep(1.5 * (attempt + 1))
        except Exception as exc:
            last = exc
            time.sleep(2.0 * (attempt + 1))
    raise last if last else RuntimeError("request failed")


def http_json(url: str) -> dict:
    return json.loads(http_text(url))


def resolve_titles(names: list[str]) -> dict[str, str | None]:
    """英文维基标题 -> 中文维基标题（批量，40 个/次）。"""
    result: dict[str, str | None] = {}
    for start in range(0, len(names), BATCH_SIZE):
        chunk = names[start : start + BATCH_SIZE]
        params = {
            "action": "query",
            "prop": "langlinks",
            "lllang": "zh",
            "lllimit": "max",
            "format": "json",
            "redirects": "1",
            "titles": "|".join(chunk),
        }
        data = http_json("https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode(params))
        query = data.get("query", {})
        normalized = {item["from"]: item["to"] for item in query.get("normalized", [])}
        redirects = {item["from"]: item["to"] for item in query.get("redirects", [])}
        by_title: dict[str, str | None] = {}
        for page in query.get("pages", {}).values():
            links = page.get("langlinks") or []
            by_title[page.get("title", "")] = links[0]["*"] if links else None

        for name in chunk:
            key = normalized.get(name, name)
            key = redirects.get(key, key)
            result[name] = by_title.get(key)
        time.sleep(0.3)
    return result


def parse_export(xml_text: str) -> dict[str, str]:
    root = ET.fromstring(xml_text.encode("utf-8"))
    for element in root.iter():
        if "}" in element.tag:
            element.tag = element.tag.split("}", 1)[1]
    pages: dict[str, str] = {}
    for page in root.findall("page"):
        title = page.findtext("title") or ""
        revision = page.find("revision")
        text = revision.findtext("text") if revision is not None else ""
        pages[title] = text or ""
    return pages


def cache_path(title: str) -> Path:
    digest = hashlib.md5(title.encode("utf-8")).hexdigest()[:16]
    return CACHE_DIR / f"{digest}.json"


def read_cache(title: str) -> dict | None:
    path = cache_path(title)
    if not path.exists():
        return None
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if cached.get("format") == 3 and cached.get("title") == title:
        return cached
    return None


def write_cache(title: str, text: str) -> dict:
    record = {"format": 3, "title": title, "text": text, "fetched": TODAY.isoformat()}
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path(title).write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    return record


def export_batch(titles: list[str]) -> dict[str, str]:
    params = {"curonly": "1", "pages": "\n".join(titles)}
    url = "https://zh.wikipedia.org/wiki/Special:Export?" + urllib.parse.urlencode(params)
    return parse_export(http_text(url))


def fetch_pages(titles: list[str], force: bool = False) -> dict[str, dict]:
    """批量取 wikitext，返回 title -> record。"""
    result: dict[str, dict] = {}
    missing: list[str] = []
    for title in titles:
        cached = None if force else read_cache(title)
        if cached:
            result[title] = cached
        else:
            missing.append(title)

    for start in range(0, len(missing), BATCH_SIZE):
        chunk = missing[start : start + BATCH_SIZE]
        pages = export_batch(chunk)
        for title in chunk:
            text = pages.get(title, "")
            result[title] = write_cache(title, text)
        time.sleep(0.3)

    return result


# ---------------------------------------------------------------------------
# 繁简转换（OpenCC TSCharacters，Apache-2.0）
# ---------------------------------------------------------------------------
def load_t2s() -> dict[str, str]:
    mapping: dict[str, str] = {}
    if not T2S_FILE.exists():
        return mapping
    for line in T2S_FILE.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        key, _, values = line.partition("\t")
        values = values.strip()
        if key and values:
            mapping[key] = values.split()[0]
    return mapping


T2S = load_t2s()


def t2s(text: str) -> str:
    return "".join(T2S.get(char, char) for char in text)


# ---------------------------------------------------------------------------
# wikitext -> 纯文本
# ---------------------------------------------------------------------------
def strip_balanced(text: str, opener: str, closer: str) -> str:
    out: list[str] = []
    index = 0
    while True:
        start = text.find(opener, index)
        if start < 0:
            out.append(text[index:])
            break
        out.append(text[index:start])
        depth = 0
        cursor = start
        while cursor < len(text):
            if text.startswith(opener, cursor):
                depth += 1
                cursor += len(opener)
            elif text.startswith(closer, cursor):
                depth -= 1
                cursor += len(closer)
                if depth == 0:
                    break
            else:
                cursor += 1
        index = cursor
    return "".join(out)


def clean_wikitext(text: str) -> str:
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"<ref[^>]*/>", "", text)
    text = re.sub(r"<ref[^>]*>.*?</ref>", "", text, flags=re.S)
    text = re.sub(r"<references[^>]*/>", "", text)
    text = strip_balanced(text, "{{", "}}")
    text = strip_balanced(text, "{|", "|}")
    text = re.sub(
        r"\[\[(?:File|Image|文件|檔案|图像|圖像|媒体|媒體):[^\]]*\]\]",
        "",
        text,
        flags=re.I,
    )
    text = re.sub(r"\[\[(?:Category|分类|分類):[^\]]*\]\]", "", text, flags=re.I)
    text = re.sub(r"\[\[[^\]|]+\|([^\]]+)\]\]", r"\1", text)
    text = re.sub(r"\[\[([^\]]+)\]\]", r"\1", text)
    text = text.replace("[[", "").replace("]]", "")
    text = re.sub(r"\[https?://[^\s\]]+\s*([^\]]*)\]", r"\1", text)
    text = re.sub(r"\[https?://[^\]]+\]", "", text)
    text = text.replace("'''", "").replace("''", "")
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"^[*#:;]+\s*", "", text, flags=re.M)
    text = re.sub(r"__[A-Z]+__", "", text)
    text = html_module.unescape(text)
    text = text.replace("芐", "苄")
    text = re.sub(r"[（(]\s*[）)]", "", text)
    text = re.sub(r"\s+([，。；：、])", r"\1", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


HEAD_RE = re.compile(r"^(={2,6})\s*(.+?)\s*\1\s*$")


def sections_from_wikitext(text: str) -> tuple[str, list[dict]]:
    intro: list[str] = []
    sections: list[dict] = []
    current: dict | None = None

    for raw in text.splitlines():
        line = raw.rstrip()
        match = HEAD_RE.match(line.strip())
        if match:
            level = len(match.group(1))
            if level in (2, 3):
                current = {"title": t2s(match.group(2).strip()), "lines": []}
                sections.append(current)
            continue
        (current["lines"] if current else intro).append(line)

    intro_text = t2s(clean_wikitext("\n".join(intro)))
    parsed = [
        {
            "title": item["title"],
            "text": t2s(clean_wikitext("\n".join(item["lines"]))),
        }
        for item in sections
    ]
    return intro_text, parsed


# ---------------------------------------------------------------------------
# 选取与组装
# ---------------------------------------------------------------------------
def pick_section(sections: list[dict], keywords: list[str], min_len: int = 70) -> str:
    best: tuple[int, str] | None = None
    for section in sections:
        text = section["text"]
        if len(text) < min_len:
            continue
        if not any(keyword in section["title"] for keyword in keywords):
            continue
        score = len(text)
        if section["title"] in ("作用机制", "作用机理", "医疗用途", "不良反应", "不良影响"):
            score += 200
        if best is None or score > best[0]:
            best = (score, text)
    return best[1] if best else ""


def trim(text: str, limit: int = 900) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    window = text[:limit]
    cut = max(window.rfind("。"), window.rfind("！"), window.rfind("？"))
    if cut < limit * 0.45:
        cut = window.rfind("；")
    if cut < limit * 0.45:
        cut = limit - 1
    out = text[: cut + 1].rstrip("，；、 ")
    if out and out[-1] not in "。！？…":
        out += "。"
    return out


def summarize(intro: str, limit: int = 98) -> str:
    intro = re.sub(r"\s+", " ", intro).strip()
    first = re.split(r"(?<=[。！？])", intro)[0].strip()
    if len(first) <= limit:
        return first
    window = first[:limit]
    cut = max(window.rfind("，"), window.rfind("；"), window.rfind("、"))
    if cut < limit * 0.5:
        cut = limit - 1
    return first[: cut + 1].rstrip("，；、") + "…"


TAG_RULES = [
    ("抗生素", ("抗生素", "抗菌药", "青霉素", "头孢")),
    ("抗病毒", ("抗病毒",)),
    ("抗真菌", ("抗真菌",)),
    ("抗疟", ("抗疟", "疟疾")),
    ("降压", ("降压", "高血压")),
    ("抗凝", ("抗凝",)),
    ("抗血小板", ("抗血小板",)),
    ("降脂", ("降脂", "他汀", "胆固醇")),
    ("降糖", ("降糖", "糖尿病")),
    ("抗肿瘤", ("抗肿瘤", "化疗", "抗癌")),
    ("免疫抑制", ("免疫抑制",)),
    ("镇痛", ("镇痛", "止痛")),
    ("抗炎", ("抗炎", "消炎")),
    ("抗抑郁", ("抗抑郁",)),
    ("抗精神病", ("抗精神病",)),
    ("抗癫痫", ("抗癫痫",)),
    ("质子泵抑制剂", ("质子泵",)),
    ("β受体阻滞剂", ("β受体", "Beta受体")),
    ("钙通道阻滞剂", ("钙通道",)),
    ("ACEI", ("血管紧张素转化酶",)),
    ("ARB", ("血管紧张素II受体", "血管紧张素Ⅱ受体")),
]


def derive_tags(text: str, category: str) -> list[str]:
    tags = []
    for tag, keywords in TAG_RULES:
        if tag == category or tag in tags:
            continue
        if any(keyword in text for keyword in keywords):
            tags.append(tag)
    return tags[:3]


def slugify(value: str) -> str:
    value = value.lower().replace("α", "alfa").replace("β", "beta")
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")


def curated_keys() -> tuple[set[str], set[str]]:
    ids: set[str] = set()
    names: set[str] = set()
    for path in CURATED_DIR.glob("*.md"):
        if path.name.startswith("_"):
            continue
        text = path.read_text(encoding="utf-8")
        id_match = re.search(r"^id:\s*(.+)$", text, re.M)
        name_match = re.search(r"^name:\s*(.+)$", text, re.M)
        if id_match:
            ids.add(id_match.group(1).strip())
        if name_match:
            names.add(re.sub(r"\s+", "", name_match.group(1).strip()))
    return ids, names


def name_from_title(title: str) -> str:
    title = re.sub(r"\s*[（(][^）)]*[）)]\s*$", "", title)
    return t2s(title)


ATOMIC_MASS = {
    "H": 1.008, "Li": 6.94, "C": 12.011, "N": 14.007, "O": 15.999,
    "F": 18.998, "Na": 22.990, "Mg": 24.305, "Al": 26.982, "Si": 28.085,
    "P": 30.974, "S": 32.06, "Cl": 35.45, "K": 39.098, "Ca": 40.078,
    "Fe": 55.845, "Cu": 63.546, "Zn": 65.38, "Br": 79.904, "Ag": 107.868,
    "I": 126.904, "Ba": 137.327, "Pt": 195.084, "Au": 196.967, "Hg": 200.592,
}


def formula_mass(formula: str) -> float | None:
    tokens = re.findall(r"([A-Z][a-z]?)(\d*)", formula)
    if not tokens:
        return None
    total = 0.0
    for symbol, count in tokens:
        if symbol not in ATOMIC_MASS:
            return None
        total += ATOMIC_MASS[symbol] * (int(count) if count else 1)
    return total


def first_drugbox(text: str) -> str:
    lower = text.lower()
    start = -1
    for marker in ("{{infobox drug", "{{drugbox", "{{infobox drugbox"):
        start = lower.find(marker)
        if start != -1:
            break
    if start == -1:
        return ""

    depth = 0
    cursor = start
    while cursor < len(text):
        if text.startswith("{{", cursor):
            depth += 1
            cursor += 2
        elif text.startswith("}}", cursor):
            depth -= 1
            cursor += 2
            if depth == 0:
                return text[start:cursor]
        else:
            cursor += 1
    return ""


def parse_chemistry(text: str) -> dict:
    block = first_drugbox(text)
    if not block:
        return {}

    result: dict = {}

    formula = ""
    match = re.search(r"\|\s*formula\s*=\s*([^\n|]+)", block, re.I)
    if match:
        formula = re.sub(r"<[^>]+>", "", match.group(1))
        formula = re.sub(r"\s+", "", formula)
        if not re.fullmatch(r"(?:[A-Z][a-z]?\d*)+", formula):
            formula = ""

    if not formula:
        counts: dict[str, int] = {}
        for symbol, count in re.findall(r"\|\s*([A-Z][a-z]?)\s*=\s*(\d+)\s*", block):
            if symbol in ATOMIC_MASS and 0 < int(count) < 300:
                counts[symbol] = counts.get(symbol, 0) + int(count)
        if counts:
            order = (["C"] if "C" in counts else []) + (["H"] if "H" in counts else [])
            order += sorted(key for key in counts if key not in ("C", "H"))
            formula = "".join(
                symbol + (str(counts[symbol]) if counts[symbol] > 1 else "")
                for symbol in order
            )

    if formula:
        result["formula"] = formula
        mass_match = re.search(r"\|\s*molecular_weight\s*=\s*([0-9.]+)", block, re.I)
        mass = float(mass_match.group(1)) if mass_match else formula_mass(formula)
        if mass:
            result["mol_mass"] = f"{mass:.2f}"

    cas_match = re.search(
        r"\|\s*CAS_number\s*=\s*([0-9]{2,7}-[0-9]{2}-[0-9])", block, re.I
    )
    if cas_match:
        result["cas"] = cas_match.group(1)

    smiles_match = re.search(r"\|\s*smiles\s*=\s*([^\n|]+)", block, re.I)
    if smiles_match:
        smiles = re.sub(r"<ref[^>]*>.*?</ref>", "", smiles_match.group(1), flags=re.S)
        smiles = re.sub(r"<[^>]+>", "", smiles)
        smiles = html_module.unescape(smiles).strip()
        if 5 < len(smiles) < 400:
            result["smiles"] = smiles

    return result


def compose(en: str, category: str, page: dict, existing_name: str = "") -> tuple[str | None, str]:
    title = page.get("title") or en
    wikitext = page.get("text", "")
    if not wikitext or wikitext.lstrip().upper().startswith("#REDIRECT"):
        return None, "redirect-or-empty"
    if "列表" in title or "消歧义" in title:
        return None, "not-a-drug"

    intro, sections = sections_from_wikitext(wikitext)
    if len(intro) < 80:
        return None, "too-short"

    uses = pick_section(
        sections,
        [
            "医疗用途",
            "醫療用途",
            "适应症",
            "適應症",
            "主治",
            "临床应用",
            "臨床應用",
            "用途",
            "应用",
            "應用",
            "功效",
            "治疗",
            "治療",
        ],
    )
    mechanism = pick_section(
        sections,
        [
            "作用机制",
            "作用機制",
            "作用机理",
            "作用機理",
            "作用机转",
            "作用機轉",
            "药理",
            "藥理",
            "机制",
            "機制",
            "原理",
        ],
    )
    safety = pick_section(
        sections,
        [
            "不良反应",
            "不良反應",
            "不良影响",
            "不良影響",
            "副作用",
            "禁忌",
            "注意事项",
            "注意事項",
            "注意",
            "安全",
            "风险",
            "風險",
            "耐受",
            "过量",
            "過量",
            "相互作用",
            "交互作用",
        ],
        min_len=50,
    )
    history = pick_section(
        sections,
        [
            "历史",
            "歷史",
            "发现",
            "發現",
            "研发",
            "研發",
            "发展",
            "發展",
            "起源",
            "发明",
            "發明",
        ],
        min_len=100,
    )
    synthesis = pick_section(
        sections,
        ["合成", "制备", "製備", "合成路线", "合成路線", "化学合成", "化學合成", "工业生产", "工業生產"],
        min_len=120,
    )

    if len(intro) < 80:
        return None, "too-short"
    if not (uses or mechanism or safety):
        return None, "missing-sections"
    total_len = len(intro) + len(uses) + len(mechanism) + len(safety) + len(history)
    if total_len < 450:
        return None, "too-thin"

    name = existing_name or name_from_title(title)
    summary = summarize(intro)
    family = f"{category}药物"
    tags = derive_tags(" ".join([mechanism, *[s["title"] for s in sections]]), category)
    brief = total_len < 1100

    body = []
    if not (uses or mechanism):
        body += ["## 简介", trim(intro, 600)]
    if history:
        body += ["## 起源与历史", trim(history, 820)]
    if mechanism:
        body += ["## 机理", trim(mechanism, 820)]
    if synthesis:
        body += ["## 合成", trim(synthesis, 820)]
    if uses:
        body += ["## 今日之用", trim(uses, 820)]
    if safety:
        body += ["## 安全边界", trim(safety, 820)]
    else:
        body += [
            "## 安全边界",
            "维基百科条目没有单独列出不良反应章节。开始用药前请阅读药品说明书，并向医生或药师确认禁忌与相互作用；出现皮疹、呼吸困难、严重腹泻等异常，应立即就医。",
        ]

    source_url = "https://zh.wikipedia.org/wiki/" + urllib.parse.quote(
        title.replace(" ", "_")
    )

    front = ["---", f"id: {slugify(en)}", f"name: {name}", f"en: {en}", f"category: {category}"]
    front.append(f"family: {family}")
    chem = parse_chemistry(wikitext)
    if chem.get("formula"):
        front.append(f"formula: {chem['formula']}")
    if chem.get("mol_mass"):
        front.append(f"mol_mass: {chem['mol_mass']}")
    if chem.get("cas"):
        front.append(f"cas: {chem['cas']}")
    if chem.get("smiles"):
        front.append(f'smiles: "{chem["smiles"]}"')
    front.append("tags: [" + ", ".join(tags) + "]")
    front.append(f"summary: {summary}")
    front.append("kind: auto")
    if brief:
        front.append("brief: true")
    front.append(f"source_title: {title}")
    front.append(f"source_url: {source_url}")
    front.append(f"updated: {TODAY.isoformat()}")
    front.append("---")

    return "\n".join(front) + "\n\n" + "\n\n".join(body) + "\n", "ok"


def strip_updated(text: str) -> str:
    return re.sub(r"^updated: .*$", "", text, flags=re.M)


def save_entry(entry_id: str, text: str, dry_run: bool = False) -> bool:
    path = AUTO_DIR / f"{entry_id}.md"
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    if old and strip_updated(old) == strip_updated(text):
        return False
    if not dry_run:
        AUTO_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return True


def read_front_matter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---", text, re.S)
    if not match:
        return {}
    meta = {}
    for line in match.group(1).splitlines():
        key, sep, value = line.partition(":")
        if sep:
            meta[key.strip()] = value.strip()
    return meta


# ---------------------------------------------------------------------------
# 模式
# ---------------------------------------------------------------------------
def interleave_seeds(seeds: list[tuple[str, str]]) -> list[tuple[str, str]]:
    groups: dict[str, list[tuple[str, str]]] = {}
    for seed in seeds:
        groups.setdefault(seed[1], []).append(seed)
    ordered: list[tuple[str, str]] = []
    while any(groups.values()):
        for items in groups.values():
            if items:
                ordered.append(items.pop(0))
    return ordered


def run_all(limit: int = 0, force: bool = False, dry_run: bool = False, target: int = 0) -> None:
    curated_ids, curated_names = curated_keys()
    seeds = []
    seen: set[str] = set()
    for en, category in SEEDS:
        entry_id = slugify(en)
        if entry_id in seen or entry_id in curated_ids:
            continue
        seen.add(entry_id)
        seeds.append((en, category, entry_id))
    seeds = interleave_seeds([(en, category) for en, category, _ in seeds])
    seeds = [(en, category, slugify(en)) for en, category in seeds]
    if limit:
        seeds = seeds[:limit]

    print(f"[resolve] {len(seeds)} seeds...", flush=True)
    resolved = resolve_titles([seed[0] for seed in seeds])

    stats = {"ok": 0, "skipped": 0, "failed": 0}
    reasons: dict[str, int] = {}
    existing = len(list(AUTO_DIR.glob("*.md"))) if AUTO_DIR.exists() else 0

    work = [(en, category, entry_id, resolved.get(en)) for en, category, entry_id in seeds]
    for start in range(0, len(work), BATCH_SIZE):
        if target and existing + stats["ok"] >= target:
            print(f"[target] reached {target} cards", flush=True)
            break

        chunk = work[start : start + BATCH_SIZE]
        titles = [item[3] for item in chunk if item[3]]
        print(f"[export] {start + 1}-{start + len(chunk)} / {len(work)}", flush=True)
        records = fetch_pages([title for title in titles if title], force=force)

        for en, category, entry_id, title in chunk:
            if not title:
                stats["failed"] += 1
                reasons["no-zh-title"] = reasons.get("no-zh-title", 0) + 1
                continue
            record = records.get(title)
            if not record:
                stats["failed"] += 1
                reasons["no-content"] = reasons.get("no-content", 0) + 1
                continue

            name_key = re.sub(r"\s+", "", name_from_title(record.get("title", "")))
            if name_key in curated_names:
                stats["skipped"] += 1
                reasons["curated-duplicate"] = reasons.get("curated-duplicate", 0) + 1
                continue

            text, reason = compose(en, category, record)
            if not text:
                stats["skipped"] += 1
                reasons[reason] = reasons.get(reason, 0) + 1
                continue
            is_new = not (AUTO_DIR / f"{entry_id}.md").exists()
            if save_entry(entry_id, text, dry_run=dry_run):
                stats["ok"] += 1
                if is_new:
                    existing += 1
            else:
                stats["skipped"] += 1
                reasons["unchanged"] = reasons.get("unchanged", 0) + 1

        print(
            f"  ok={stats['ok']} skipped={stats['skipped']} failed={stats['failed']}",
            flush=True,
        )

    print("\n== done ==")
    print(f"written: {stats['ok']}  skipped: {stats['skipped']}  failed: {stats['failed']}")
    for reason, count in sorted(reasons.items(), key=lambda item: -item[1]):
        print(f"  {reason}: {count}")


def run_upcoming(days: int = 7, force: bool = True) -> None:
    import build

    drugs = build.load_drugs()
    total = len(drugs)
    today = datetime.now(TZ).date()
    targets = []
    for offset in range(days):
        day = today + timedelta(days=offset)
        drug = drugs[build.cycle_index(day, total)]
        if drug.get("kind") == "auto":
            targets.append(drug)

    print(f"[refresh] {len(targets)} upcoming auto cards", flush=True)
    records = fetch_pages([drug["source_title"] for drug in targets], force=force)
    for drug in targets:
        record = records.get(drug.get("source_title", ""))
        if not record:
            print(f"  skipped {drug['name']}: no-content", flush=True)
            continue
        text, reason = compose(drug["en"], drug["category"], record, existing_name=drug["name"])
        if text:
            save_entry(drug["id"], text)
            print(f"  refreshed {drug['name']}", flush=True)
        else:
            print(f"  skipped {drug['name']}: {reason}", flush=True)


def run_ids(ids: list[str], force: bool = True) -> None:
    entries = []
    for entry_id in ids:
        path = AUTO_DIR / f"{entry_id}.md"
        if not path.exists():
            print(f"  missing {entry_id}")
            continue
        meta = read_front_matter(path)
        entries.append((entry_id, meta))
    records = fetch_pages([meta.get("source_title", "") for _, meta in entries], force=force)
    for entry_id, meta in entries:
        record = records.get(meta.get("source_title", ""))
        if not record:
            print(f"  skipped {entry_id}: no-content")
            continue
        text, reason = compose(meta.get("en", entry_id), meta.get("category", "其他"), record, existing_name=meta.get("name", ""))
        if text:
            save_entry(entry_id, text)
            print(f"  refreshed {entry_id}")
        else:
            print(f"  skipped {entry_id}: {reason}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate drug cards from zh.wikipedia.org")
    parser.add_argument("--all", action="store_true", help="按种子表批量生成")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 条（调试用）")
    parser.add_argument("--target", type=int, default=0, help="达到 N 张资料卡后停止")
    parser.add_argument("--force", action="store_true", help="忽略本地缓存")
    parser.add_argument("--dry-run", action="store_true", help="只统计，不写文件")
    parser.add_argument("--upcoming", type=int, default=0, help="刷新未来 N 天要登场的资料卡")
    parser.add_argument("--ids", default="", help="刷新指定 id，逗号分隔")
    args = parser.parse_args()

    if args.upcoming:
        run_upcoming(args.upcoming, force=not args.dry_run)
    elif args.ids:
        run_ids([item.strip() for item in args.ids.split(",") if item.strip()])
    elif args.all:
        run_all(limit=args.limit, force=args.force, dry_run=args.dry_run, target=args.target)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
