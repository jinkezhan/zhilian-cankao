"""方言文字归一化器（第 9 步）。

只做一件事：把方言口语词替换成普通话书面词，供下游意图解析使用。
不调用模型、不读写业务数据、不改写句子结构，便于单测与复现。

用法：
    from dialect_normalize import normalize
    info = normalize("3 号店 834 未来七天卖几多？")
    info["normalized"]   # "3 号店 834 未来七天卖 多少？"
    info["hits"]         # [{"word": "几多", "standard": "多少", ...}]
    info["skipped"]      # 因误伤排除而跳过的命中

mode：
    "auto"（默认）   common + 所有方言条目一起匹配
    "<方言 key>"      只匹配 common + 该方言（key 见 config/dialect_glossary.yaml）
    "none"            不替换，原样返回

覆盖范围就是词表里写了的方言，没写的不支持。
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

BASE_DIR = Path(__file__).resolve().parents[1]
GLOSSARY_PATH = BASE_DIR / "config" / "dialect_glossary.yaml"
DISABLED_ENV = "ZHILIAN_DIALECT_DISABLED"  # 逗号分隔的方言 key，用于 holdout 对照实验


class GlossaryError(ValueError):
    """词表或 mode 不合法。"""


@lru_cache(maxsize=8)
def _load_cached(path_str: str, disabled_str: str) -> dict[str, Any]:
    """按 (路径, 禁用方言) 缓存；文件小，但测试会反复调用。"""
    path = Path(path_str)
    if not path.exists():
        raise GlossaryError(f"找不到方言词表：{path}")
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise GlossaryError("方言词表顶层必须是映射（key: value）")

    disabled = {
        x.strip().strip("\"'") for x in disabled_str.split(",") if x.strip()
    }
    dialects = data.get("dialects") or {}
    if not isinstance(dialects, dict):
        raise GlossaryError("dialects 必须是映射")

    cleaned: dict[str, Any] = {}
    for key, block in dialects.items():
        if key in disabled:
            continue
        block = block or {}
        entries = block.get("entries") or []
        cleaned[key] = {
            "name": str(block.get("name", key)),
            "entries": [
                (str(e["word"]), str(e["standard"]))
                for e in entries
                if isinstance(e, dict) and e.get("word") and e.get("standard")
            ],
        }

    exclusions: dict[str, list[str]] = {}
    for item in data.get("exclusions") or []:
        if not isinstance(item, dict) or not item.get("word"):
            continue
        keep = [str(x) for x in (item.get("keep_in") or []) if x]
        exclusions[str(item["word"])] = keep

    common = [
        (str(e["word"]), str(e["standard"]))
        for e in (data.get("common") or [])
        if isinstance(e, dict) and e.get("word") and e.get("standard")
    ]

    return {
        "version": data.get("glossary_version", 1),
        "common": common,
        "dialects": cleaned,
        "exclusions": exclusions,
        "disabled": sorted(disabled),
    }


def load_glossary(
    path: str | Path | None = None,
    disabled_dialects: list[str] | None = None,
) -> dict[str, Any]:
    """加载词表。

    disabled_dialects 优先；否则读环境变量 ZHILIAN_DIALECT_DISABLED，
    再否则读 YAML 的 disabled_dialects。holdout 实验用环境变量切换，
    不需要改配置文件、更不需要改代码。
    """
    path = Path(path) if path is not None else GLOSSARY_PATH
    if disabled_dialects is not None:
        disabled_str = ",".join(disabled_dialects)
    else:
        env_value = os.environ.get(DISABLED_ENV, "").strip()
        if env_value:
            disabled_str = env_value
        else:
            raw = path.exists() and path.read_text(encoding="utf-8") or ""
            disabled_str = ""
            for line in raw.splitlines():
                s = line.strip()
                if s.startswith("disabled_dialects:"):
                    tail = s.split(":", 1)[1].strip()
                    if tail.startswith("[") and tail.endswith("]"):
                        disabled_str = ",".join(
                            part.strip().strip("\"'")
                            for part in tail[1:-1].split(",")
                            if part.strip()
                        )
                    break
    return _load_cached(str(path), disabled_str)


def available_modes(glossary: dict[str, Any] | None = None) -> list[str]:
    """可用于 mode 的取值：auto / none / 各方言 key。"""
    g = glossary or load_glossary()
    return ["auto", "none", *sorted(g["dialects"])]


def dialect_names(glossary: dict[str, Any] | None = None) -> dict[str, str]:
    """方言 key -> 中文名（界面展示用；不要让模型自己判断方言种类）。"""
    g = glossary or load_glossary()
    return {k: v["name"] for k, v in g["dialects"].items()}


def _build_candidates(
    glossary: dict[str, Any], mode: str
) -> list[tuple[str, str, str]]:
    """返回 [(word, standard, source)]，按词长降序（同长保持词表顺序）。

    同一个词同时出现在 common 和某个方言里时只保留先出现的（common 优先），
    避免同一位置产生两条命中。
    """
    if mode == "none":
        return []
    if mode != "auto" and mode not in glossary["dialects"]:
        raise GlossaryError(
            f"未知 dialect：{mode}（可选：auto / none / "
            + " / ".join(sorted(glossary["dialects"]))
            + "）"
        )

    items: list[tuple[str, str, str]] = [
        (w, s, "common") for w, s in glossary["common"]
    ]
    dialect_keys = (
        sorted(glossary["dialects"]) if mode == "auto" else [mode]
    )
    for key in dialect_keys:
        for w, s in glossary["dialects"][key]["entries"]:
            items.append((w, s, key))

    seen: set[str] = set()
    deduped: list[tuple[str, str, str]] = []
    for word, standard, source in items:
        if word in seen:
            continue
        seen.add(word)
        deduped.append((word, standard, source))

    deduped.sort(key=lambda x: len(x[0]), reverse=True)
    return deduped


def _keep_ranges(text: str, words: list[str]) -> list[tuple[int, int, str]]:
    """找出所有保护词的覆盖区间（用于误伤排除）。"""
    ranges: list[tuple[int, int, str]] = []
    for word in words:
        if not word:
            continue
        start = text.find(word)
        while start != -1:
            ranges.append((start, start + len(word), word))
            start = text.find(word, start + 1)
    return ranges


def normalize(
    text: str,
    mode: str = "auto",
    glossary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """把方言词替换成普通话词。

    返回：
        original   原文
        normalized 归一化文本（mode=none 或没有命中时与原文完全一致）
        hits       [{word, standard, start, end, source}]，start/end 是原文坐标
        skipped    [{word, standard, start, end, reason, protected_by}]
        mode       实际使用的 mode
    """
    text = "" if text is None else str(text)
    g = glossary or load_glossary()
    if mode not in ("auto", "none") and mode not in g["dialects"]:
        raise GlossaryError(
            f"未知 dialect：{mode}（可选：auto / none / "
            + " / ".join(sorted(g["dialects"]))
            + "）"
        )

    result: dict[str, Any] = {
        "original": text,
        "normalized": text,
        "hits": [],
        "skipped": [],
        "mode": mode,
    }
    if mode == "none" or not text:
        return result

    candidates = _build_candidates(g, mode)
    exclusions = g["exclusions"]
    # 先把所有保护词的区间算出来，后面每次命中只做区间包含判断
    keep_cache: dict[str, list[tuple[int, int, str]]] = {}

    hits: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    i = 0
    n = len(text)
    while i < n:
        matched = None
        for word, standard, source in candidates:
            if text.startswith(word, i):
                matched = (word, standard, source)
                break
        if matched is None:
            i += 1
            continue

        word, standard, source = matched
        keep_words = exclusions.get(word, [])
        blocked_by = None
        if keep_words:
            if word not in keep_cache:
                keep_cache[word] = _keep_ranges(text, keep_words)
            end = i + len(word)
            for r_start, r_end, keep_word in keep_cache[word]:
                if i >= r_start and end <= r_end:
                    blocked_by = keep_word
                    break

        if blocked_by is not None:
            skipped.append(
                {
                    "word": word,
                    "standard": standard,
                    "start": i,
                    "end": i + len(word),
                    "reason": "exclusion",
                    "protected_by": blocked_by,
                }
            )
            # 只前进一个字符：保护词内部可能还有别的独立词，但都会被同一区间挡住
            i += 1
            continue

        hits.append(
            {
                "word": word,
                "standard": standard,
                "start": i,
                "end": i + len(word),
                "source": source,
            }
        )
        i += len(word)

    result["skipped"] = skipped
    if not hits:
        return result

    pieces: list[str] = []
    cursor = 0
    for hit in hits:
        pieces.append(text[cursor : hit["start"]])
        pieces.append(hit["standard"])
        cursor = hit["end"]
    pieces.append(text[cursor:])

    result["normalized"] = "".join(pieces)
    result["hits"] = hits
    result["skipped"] = skipped
    return result


if __name__ == "__main__":  # 手工试词：python app/dialect_normalize.py "恁子搞"
    import sys

    sample = " ".join(sys.argv[1:]) or "3 号店 834 未来七天卖几多？恁子搞？"
    info = normalize(sample)
    print(f"原文：{info['original']}")
    print(f"归一：{info['normalized']}")
    for h in info["hits"]:
        print(f"  命中 {h['word']} -> {h['standard']} [{h['source']}]")
    for s in info["skipped"]:
        print(f"  跳过 {s['word']}（保护词：{s['protected_by']}）")
