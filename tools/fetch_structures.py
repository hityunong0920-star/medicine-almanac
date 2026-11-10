#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 PubChem 抓取二维结构式，保存为 site/assets/structures/<id>.png。

用法：
    python tools/fetch_structures.py            # 补齐缺失的结构图
    python tools/fetch_structures.py --force    # 全部重抓
    python tools/fetch_structures.py --limit 5  # 先试几条

查询顺序：CAS 号 -> 英文名 -> SMILES。没有分子式的条目（蛋白、抗体等）跳过。
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTENT_DIRS = (ROOT / "content" / "drugs", ROOT / "content" / "auto")
OUTPUT_DIR = ROOT / "site" / "assets" / "structures"
UA = "MedicineAlmanac/0.5 (educational; low-volume; contact via GitHub)"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def read_meta(path: Path) -> dict:
    text = path.read_text(encoding="utf-8").lstrip("\ufeff")
    match = re.match(r"^---\n(.*?)\n---", text, re.S)
    if not match:
        return {}
    meta = {}
    for line in match.group(1).splitlines():
        key, sep, value = line.partition(":")
        if sep:
            meta[key.strip()] = value.strip().strip('"')
    return meta


def request_png(url: str, data: bytes | None = None) -> bytes | None:
    last = None
    for attempt in range(4):
        try:
            request = urllib.request.Request(
                url,
                data=data,
                headers={
                    "User-Agent": UA,
                    "Accept": "image/png",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )
            with urllib.request.urlopen(request, timeout=45) as response:
                payload = response.read()
            if payload.startswith(PNG_MAGIC) and len(payload) > 500:
                return payload
            return None
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code == 404:
                return None
            time.sleep(1.2 * (attempt + 1))
        except Exception as exc:
            last = exc
            time.sleep(1.5 * (attempt + 1))
    if last:
        print(f"    ! {type(last).__name__}", flush=True)
    return None


def fetch_structure(meta: dict) -> bytes | None:
    for key in ("cas", "en"):
        value = meta.get(key, "").strip()
        if not value:
            continue
        url = (
            "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/"
            + urllib.parse.quote(value)
            + "/PNG?record_type=2d&image_size=500x500"
        )
        payload = request_png(url)
        if payload:
            return payload

    smiles = meta.get("smiles", "").strip()
    if smiles and len(smiles) < 1500:
        body = urllib.parse.urlencode({"smiles": smiles}).encode("utf-8")
        url = (
            "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/smiles/PNG"
            "?record_type=2d&image_size=500x500"
        )
        payload = request_png(url, data=body)
        if payload:
            return payload

    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch 2D structures from PubChem")
    parser.add_argument("--force", action="store_true", help="覆盖已有图片")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 条")
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    files = []
    for directory in CONTENT_DIRS:
        if directory.exists():
            files.extend(sorted(directory.glob("*.md")))
    files = [path for path in files if not path.name.startswith("_")]
    if args.limit:
        files = files[: args.limit]

    fetched = skipped = failed = 0
    for index, path in enumerate(files, start=1):
        meta = read_meta(path)
        drug_id = meta.get("id", path.stem)
        target = OUTPUT_DIR / f"{drug_id}.png"

        if target.exists() and not args.force:
            skipped += 1
            continue
        if not meta.get("formula"):
            skipped += 1
            continue

        payload = fetch_structure(meta)
        if payload:
            target.write_bytes(payload)
            fetched += 1
        else:
            failed += 1

        if index % 25 == 0:
            print(
                f"  [{index}/{len(files)}] fetched={fetched} skipped={skipped} failed={failed}",
                flush=True,
            )
        time.sleep(0.3 + random.random() * 0.2)

    print(f"\nfetched={fetched}  skipped={skipped}  failed={failed}")


if __name__ == "__main__":
    main()
