#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""译腔与 AI 味自检。

用法：
    python translate_lint.py <文件> [--lang zh|en|auto] [--json] [--max-show N]

只报可疑处，不判对错。命中项必须人工复核：引文、专名、术语的固定说法
常被误报（脚本会把这些标为 low）。
"""

import argparse
import json
import os
import re
import sys

# ---------------------------------------------------------------- 中文规则

# (id, 严重度, 说明, 正则)
ZH_PATTERNS = [
    ("ZH-01", "high", "虚化动词：把动作包进名词里，句子失去施动者",
     r"(进行|作出|给予|加以|予以|开展|实施)[了]?(一[个次步]?)?[\u4e00-\u9fa5]{2,6}"),
    ("ZH-02", "high", "「对……进行/加以」句式，动作被架空",
     r"对[\u4e00-\u9fa5、，]{2,20}(进行|加以|作出|予以)"),
    ("ZH-03", "high", "「被」字句：汉语的被动带受损色彩，多数英语被动句应改无主句或主动句",
     r"被[\u4e00-\u9fa5]{1,6}(所)?[\u4e00-\u9fa5]{0,4}"),
    ("ZH-04", "high", "「性/度/化」堆叠：抽象名词套壳",
     r"[\u4e00-\u9fa5]{2}(性|化)(的)?[\u4e00-\u9fa5]{0,4}(性|化)"),
    ("ZH-05", "high", "AI 套话：空承诺、空强化",
     r"(具有(重要|深远|重大)(的)?(理论|现实|指导)?(意义|价值)|极大地|深刻地|充分地|具有重要意义|意义重大|影响深远)"),
    ("ZH-06", "high", "AI 套话：铺垫式开头",
     r"(在当今社会|随着[\u4e00-\u9fa5]{2,12}的(快速|迅猛|不断|持续)?发展|近年来，|众所周知|毋庸置疑)"),
    ("ZH-07", "high", "AI 套话：元话语与总结腔",
     r"(综上所述|总之|总而言之|值得注意的是|需要指出的是|不难发现|由此可见|本文认为|笔者以为)"),
    ("ZH-08", "mid", "连接词滥用：译腔的典型标记，汉语靠语序与意合",
     r"(因此|然而|此外|另外|同时|而且|并且|从而)[，,]"),
    ("ZH-09", "mid", "「通过……来/以」：英语 by doing 的直搬",
     r"通过[\u4e00-\u9fa5，]{2,20}(来|以|去)"),
    ("ZH-10", "mid", "「作为……的……」：as a ... 的直搬",
     r"作为(一[个种名位])?[\u4e00-\u9fa5]{2,12}的"),
    ("ZH-11", "mid", "代词「它/它们」：英语回指习惯，汉语倾向省略或重复名词",
     r"(它们|它的|它们|它们)"),
    ("ZH-12", "mid", "「不仅……而且……」结构填充",
     r"不仅[\u4e00-\u9fa5，]{2,30}(而且|还|也)"),
    ("ZH-13", "low", "「的」字连缀嫌疑（三连及以上），需人工确认",
     r"的[\u4e00-\u9fa5]{1,6}的[\u4e00-\u9fa5]{1,6}的"),
    ("ZH-14", "low", "欧化插入语：英语同位语/分词短语的直搬",
     r"[，,][\u4e00-\u9fa5]{2,12}[，,][\u4e00-\u9fa5]{2,12}[，,]"),
    ("ZH-15", "mid", "「……化」的动词化外壳，常见于政策体译文",
     r"(进一步|不断|持续|深入)?(推进|推动|促进|实现|加强|完善|优化|提升)[\u4e00-\u9fa5]{2,10}(建设|发展|水平|能力|体系)"),
]

# ---------------------------------------------------------------- 英文规则

EN_PATTERNS = [
    ("EN-01", "high", "AI 高频词：换成具体的动词或名词",
     r"\b(delve[sd]?|tapestry|pivotal|crucial|underscore[sd]?|realm|multifaceted|"
     r"testament|meticulous(ly)?|intricate|paramount|seamless(ly)?|holistic|synergy|"
     r"leverage[sd]?|foster(s|ed|ing)?|embark(s|ed|ing)?|navigat(e|es|ed|ing) the|"
     r"landscape of|robust(ly)?|profound(ly)?|vibrant|myriad)\b"),
    ("EN-02", "high", "空起句：删掉后信息不减少",
     r"(It is important to note that|It is worth noting that|It should be noted that|"
     r"In today's [a-z]+|It is no secret that|Needless to say)"),
    ("EN-03", "high", "空转折/空递进：句首连接词密度过高（每段一个足够）",
     r"(?:^|[.!?][\"']?\s+)(Moreover|Furthermore|Additionally|In addition|Consequently|Notably)[,]"),
    ("EN-04", "high", "套话收尾",
     r"(In conclusion|To sum up|All in all|Taken together, this|Overall, these findings)"),
    ("EN-05", "mid", "模糊归因：谁认为？",
     r"\b(it is widely (believed|acknowledged|recognized)|scholars have (paid|noted)|"
     r"research has shown that|studies have found that)\b"),
    ("EN-06", "mid", "「不仅……而且」堆叠",
     r"not only[\s\S]{2,80}but also"),
    ("EN-07", "mid", "弱势动词 + 名词化：改成强势动词",
     r"\b(conduct(ed|s)?|perform(ed|s)?|carry out|make an? (attempt|effort)|"
     r"provide an? (analysis|understanding|overview) of|give (consideration|rise) to)\b"),
    ("EN-08", "mid", "of 链：三个以上 of 嵌套，读者找不到主干",
     r"\bof\b[^.]{0,30}\bof\b[^.]{0,30}\bof\b"),
    ("EN-09", "low", "破折号/冒号密度，学术写作宜节制",
     r"(--|—|–)"),
    ("EN-10", "mid", "空洞评价形容词",
     r"\b(significant(ly)?|substantial(ly)?|considerable|remarkable|"
     r"extraordinary|incredible|amazing)\b"),
    ("EN-11", "low", "被动 + 名词化组合，施动者消失（学术方法节除外）",
     r"\b(is|are|was|were) [a-z]+ed (by|through|via) (the )?[a-z]+(tion|ment|ance|ence|ity)\b"),
    ("EN-12", "mid", "三连排比（A, B, and C 全同构），AI 味的典型节奏",
     r"\b[a-z]+(ly)?, [a-z]+(ly)?, and [a-z]+(ly)?\b"),
]

ZH_SENT_SPLIT = re.compile(r"(?<=[。！？；\n])")
EN_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")


def read_text(path):
    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        return f.read()


def guess_lang(text):
    cjk = len(re.findall(r"[\u4e00-\u9fa5]", text))
    latin = len(re.findall(r"[A-Za-z]", text))
    return "zh" if cjk > latin * 0.5 else "en"


def line_of(text, idx):
    return text.count("\n", 0, idx) + 1


def scan_patterns(text, patterns, lang):
    hits = []
    for pid, sev, hint, pat in patterns:
        flags = re.MULTILINE | re.IGNORECASE
        for m in re.finditer(pat, text, flags):
            frag = m.group(0).replace("\n", " ").strip()
            if len(frag) > 60:
                frag = frag[:60] + "…"
            hits.append({
                "id": pid, "severity": sev, "line": line_of(text, m.start()),
                "text": frag, "hint": hint, "lang": lang,
            })
    return hits


def split_sentences(text, lang):
    parts = ZH_SENT_SPLIT.split(text) if lang == "zh" else EN_SENT_SPLIT.split(text)
    return [p.strip() for p in parts if p.strip()]


def scan_metrics(text, lang):
    """按句统计的超标项：句长、的密度、名词化密度、被动密度。"""
    hits = []
    for s in split_sentences(text, lang):
        n_char = len(re.sub(r"\s", "", s))
        if lang == "zh":
            if n_char > 60:
                hits.append({
                    "id": "ZH-M1", "severity": "high", "line": line_of(text, text.find(s)),
                    "text": s[:50] + "…", "hint": "长句（%d 字）：汉语超过 40 字就该断" % n_char,
                    "lang": lang,
                })
            n_de = s.count("的")
            if n_de >= 4:
                hits.append({
                    "id": "ZH-M2", "severity": "mid", "line": line_of(text, text.find(s)),
                    "text": "「的」×%d：%s…" % (n_de, s[:40]),
                    "hint": "「的」字过多：把定语改成小句，或换主语", "lang": lang,
                })
            if s.count("，") >= 4:
                hits.append({
                    "id": "ZH-M3", "severity": "mid", "line": line_of(text, text.find(s)),
                    "text": s[:40] + "…",
                    "hint": "一逗到底（%d 个逗号）：断句，重起主语" % s.count("，"),
                    "lang": lang,
                })
        else:
            words = s.split()
            n_w = len(words)
            if n_w > 34:
                hits.append({
                    "id": "EN-M1", "severity": "mid", "line": line_of(text, text.find(s)),
                    "text": " ".join(words[:12]) + "…",
                    "hint": "长句（%d 词）：拆句，或把从句改成独立句" % n_w,
                    "lang": lang,
                })
            nom = len(re.findall(r"\b\w+(tion|ment|ance|ence|ity|ness|ization)\b", s, re.I))
            if nom >= 4:
                hits.append({
                    "id": "EN-M2", "severity": "high", "line": line_of(text, text.find(s)),
                    "text": " ".join(words[:12]) + "…",
                    "hint": "名词化密度 %d：还原一两个为动词，把施动者找回来" % nom,
                    "lang": lang,
                })
            passive = len(re.findall(r"\b(is|are|was|were|been|being)\s+\w+ed\b", s, re.I))
            if n_w >= 12 and passive >= 2:
                hits.append({
                    "id": "EN-M3", "severity": "mid", "line": line_of(text, text.find(s)),
                    "text": " ".join(words[:12]) + "…",
                    "hint": "被动密度 %d：问一句「谁做的」，多数句子可改主动" % passive,
                    "lang": lang,
                })
    return hits


def report(hits, lang, max_show):
    order = {"high": 0, "mid": 1, "low": 2}
    hits.sort(key=lambda h: (order.get(h["severity"], 3), h["line"]))
    counts = {"high": 0, "mid": 0, "low": 0}
    for h in hits:
        counts[h["severity"]] = counts.get(h["severity"], 0) + 1

    title = "中文译腔自检" if lang == "zh" else "英文 AI 味自检"
    print("=" * 62)
    print("%s：命中 %d 处（high %d / mid %d / low %d）" %
          (title, len(hits), counts["high"], counts["mid"], counts["low"]))
    print("=" * 62)
    if not hits:
        print("没有命中。仍然建议出声读一遍，脚本不判语义。")
        return
    for h in hits[:max_show]:
        print("\n[%s|%s] 第 %d 行：%s" % (h["id"], h["severity"], h["line"], h["text"]))
        print("    → %s" % h["hint"])
    if len(hits) > max_show:
        print("\n…… 另有 %d 处未显示（--max-show 调整）" % (len(hits) - max_show))
    print("\n提示：high 项逐条看；low 项多为误报（引文、专名、固定术语）。")


def main():
    ap = argparse.ArgumentParser(description="译腔与 AI 味自检")
    ap.add_argument("path", help="待检文件（UTF-8）")
    ap.add_argument("--lang", choices=["zh", "en", "auto"], default="auto")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--max-show", type=int, default=40)
    args = ap.parse_args()

    if not os.path.exists(args.path):
        print("找不到文件：%s" % args.path, file=sys.stderr)
        return 2

    text = read_text(args.path)
    lang = guess_lang(text) if args.lang == "auto" else args.lang
    patterns = ZH_PATTERNS if lang == "zh" else EN_PATTERNS
    hits = scan_patterns(text, patterns, lang) + scan_metrics(text, lang)

    if args.json:
        print(json.dumps({"lang": lang, "count": len(hits), "hits": hits},
                         ensure_ascii=False, indent=2))
    else:
        report(hits, lang, args.max_show)
    return 0


if __name__ == "__main__":
    sys.exit(main())
