#!/usr/bin/env python3
r"""
将文件夹中的电子书按「章节」导出为 UTF-8 文本，供 GraphRAG 等管线使用。

当前支持：
  - PDF：依赖 pymupdf (fitz)

切分策略（--mode）：
  outline      使用 PDF 书签/大纲（有书签时最可靠）
  regex        使用自定义正则；第一个捕获组作为章节 id（见 --pattern）
  treatment    匹配行首「TREATMENT 4.3.1」类标题（适用于本手册体例）
  numbered     匹配行首「1.2.3 标题」；自动跳过点线目录行，并可跳过前若干页、
               跳过「单页内编号行过多」的矩阵汇总页
  compound     numbered + treatment 合并去重（对本目录下该 PDF 的默认推荐）
  none         整本书一个 txt

用法示例：
  python books_to_graphrag_txt.py --input . --output ./graphrag_input
  python books_to_graphrag_txt.py --input . --output ./out --mode outline
  python books_to_graphrag_txt.py --input . --output ./out --mode regex --pattern "(?m)^(Chapter\s+\d+[^\n]*)"
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple


def _slug(s: str, max_len: int = 120) -> str:
    s = s.strip()
    s = re.sub(r'[/\\:*?"<>|]+', "_", s)
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"_+", "_", s).strip("._")
    return (s[:max_len] if s else "section") or "section"


@dataclass(frozen=True)
class SplitPoint:
    char_index: int
    label: str


def _page_char_spans(pages_text: Sequence[str]) -> List[Tuple[int, int, int]]:
    """返回 [(start, end, page_index0), ...]，page 文本之间用 \\n 拼接。"""
    spans: List[Tuple[int, int, int]] = []
    offset = 0
    for i, t in enumerate(pages_text):
        chunk = t if i == 0 else "\n" + t
        start = offset
        offset += len(chunk)
        spans.append((start, offset, i))
    return spans


def _char_page(spans: Sequence[Tuple[int, int, int]], pos: int) -> int:
    for start, end, p in spans:
        if start <= pos < end:
            return p
    return spans[-1][2] if spans else 0


def _heavy_numbered_pages(
    pages_text: Sequence[str],
    *,
    line_pat: re.Pattern[str],
    min_hits: int,
) -> set[int]:
    bad: set[int] = set()
    for i, t in enumerate(pages_text):
        hits = 0
        for m in line_pat.finditer(t):
            line = m.group(0)
            if re.search(r"\.{4,}", line):
                continue
            title = m.group(2).strip()
            if len(title) < 6:
                continue
            hits += 1
        if hits >= min_hits:
            bad.add(i)
    return bad


def extract_pdf_pages(path: Path) -> List[str]:
    import fitz  # pymupdf

    doc = fitz.open(path)
    try:
        return [doc[i].get_text() for i in range(doc.page_count)]
    finally:
        doc.close()


def split_pdf_by_outline(path: Path) -> List[Tuple[str, str]]:
    import fitz

    doc = fitz.open(path)
    try:
        toc = doc.get_toc()
        if not toc:
            return []

        def flatten(items, depth=0):
            out = []
            for it in items:
                if isinstance(it[0], (list, tuple)) and len(it) == 1:
                    out.extend(flatten(it[0], depth))
                    continue
                lvl, title, p1 = it[0], it[1], it[2]
                out.append((int(lvl), str(title), int(p1)))
                if len(it) > 3 and isinstance(it[3], list):
                    out.extend(flatten(it[3], depth + 1))
            return out

        flat = flatten(toc)
        chapters: List[Tuple[str, str]] = []
        for i, (_lvl, title, start_p) in enumerate(flat):
            start_p = max(0, start_p - 1)
            end_p = (
                flat[i + 1][2] - 1
                if i + 1 < len(flat)
                else doc.page_count - 1
            )
            end_p = max(start_p, min(end_p, doc.page_count - 1))
            parts = []
            for p in range(start_p, end_p + 1):
                parts.append(doc[p].get_text())
            body = "\n\n".join(parts)
            chapters.append((title, body))
        return chapters
    finally:
        doc.close()


def _merge_split_points(points: Sequence[SplitPoint]) -> List[SplitPoint]:
    ordered = sorted(points, key=lambda x: x.char_index)
    merged: List[SplitPoint] = []
    for p in ordered:
        if merged and p.char_index == merged[-1].char_index:
            if len(p.label) > len(merged[-1].label):
                merged[-1] = p
            continue
        merged.append(p)
    return merged


def split_full_text(
    full_text: str,
    pages_text: Sequence[str],
    *,
    mode: str,
    custom_pattern: Optional[str],
    skip_first_pages: int,
    heavy_page_threshold: int,
) -> List[Tuple[str, str]]:
    spans = _page_char_spans(pages_text)
    line_numbered = re.compile(
        r"^(\d+\.\d+\.\d+)\s+([^\n]+)$", re.MULTILINE
    )
    heavy_pages = _heavy_numbered_pages(
        pages_text,
        line_pat=line_numbered,
        min_hits=heavy_page_threshold,
    )
    treatment_line = re.compile(
        r"^TREATMENT\s+(\d+\.\d+\.\d+)\s*$", re.MULTILINE | re.IGNORECASE
    )

    points: List[SplitPoint] = []

    def add_numbered():
        for m in line_numbered.finditer(full_text):
            line = m.group(0)
            if re.search(r"\.{4,}", line):
                continue
            title = m.group(2).strip()
            if len(title) < 6:
                continue
            page = _char_page(spans, m.start())
            if page < skip_first_pages:
                continue
            if page in heavy_pages:
                continue
            sec = m.group(1)
            points.append(
                SplitPoint(m.start(), f"{sec} {title}".strip())
            )

    def add_treatment():
        for m in treatment_line.finditer(full_text):
            sec = m.group(1)
            points.append(SplitPoint(m.start(), f"TREATMENT_{sec}"))

    if mode == "none":
        return [("full", full_text)]

    if mode == "treatment":
        add_treatment()
    elif mode == "numbered":
        add_numbered()
    elif mode == "compound":
        add_numbered()
        add_treatment()
    elif mode == "regex":
        if not custom_pattern:
            raise SystemExit("--mode regex 时必须提供 --pattern")
        rx = re.compile(custom_pattern, re.MULTILINE)
        for m in rx.finditer(full_text):
            label = m.group(1) if m.lastindex else m.group(0)
            points.append(SplitPoint(m.start(), str(label).strip()))
    else:
        raise SystemExit(f"未知 mode: {mode}")

    merged = _merge_split_points(points)
    if not merged:
        return [("full", full_text)]

    chapters: List[Tuple[str, str]] = []
    for i, sp in enumerate(merged):
        end = merged[i + 1].char_index if i + 1 < len(merged) else len(full_text)
        chunk = full_text[sp.char_index : end].strip()
        if chunk:
            chapters.append((sp.label, chunk))

    if merged and merged[0].char_index > 0:
        pre = full_text[: merged[0].char_index].strip()
        if pre:
            chapters.insert(0, ("front_matter", pre))

    return chapters


def write_book(
    book_stem: str,
    chapters: Sequence[Tuple[str, str]],
    out_root: Path,
) -> None:
    book_dir = out_root / _slug(book_stem, 80)
    book_dir.mkdir(parents=True, exist_ok=True)
    width = max(3, len(str(len(chapters))))
    for i, (title, body) in enumerate(chapters, start=1):
        fname = f"{i:0{width}d}_{_slug(title, 100)}.txt"
        path = book_dir / fname
        header = f"书名: {book_stem}\n章节: {title}\n\n"
        text = header + body
        path.write_text(text, encoding="utf-8")


def iter_inputs(folder: Path, exts: Sequence[str]) -> Iterable[Path]:
    ext_set = {e.lower().lstrip(".") for e in exts}
    for p in sorted(folder.iterdir()):
        if p.is_file() and p.suffix.lower().lstrip(".") in ext_set:
            yield p


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="将书籍按章节导出为 txt（GraphRAG 输入）"
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("."),
        help="含电子书文件的文件夹",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("graphrag_txt"),
        help="输出根目录（每本书一个子文件夹）",
    )
    parser.add_argument(
        "--mode",
        choices=[
            "auto",
            "outline",
            "compound",
            "numbered",
            "treatment",
            "regex",
            "none",
        ],
        default="auto",
        help="auto：有 PDF 书签用 outline，否则 compound",
    )
    parser.add_argument(
        "--pattern",
        default=None,
        help="regex 模式（仅 mode=regex）；应用 re.MULTILINE；建议含一个捕获组作为章节名",
    )
    parser.add_argument(
        "--skip-first-pages",
        type=int,
        default=9,
        help="numbered/compound 时忽略前 N 页内的「x.x.x 标题」匹配（去掉目录伪标题）",
    )
    parser.add_argument(
        "--heavy-page-threshold",
        type=int,
        default=8,
        help="若单页内 x.x.x 标题行数≥该值，则整页不参与 numbered 切分（去掉矩阵汇总页）",
    )
    parser.add_argument(
        "--ext",
        default="pdf",
        help="处理的扩展名，逗号分隔，默认仅 pdf",
    )
    parser.add_argument(
        "--page-markers",
        action="store_true",
        help="拼接全文时在每页之间插入 --- Page N ---，便于追溯页码",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    if not args.input.is_dir():
        print(f"输入不是文件夹: {args.input}", file=sys.stderr)
        return 2

    exts = [x.strip() for x in args.ext.split(",") if x.strip()]
    files = list(iter_inputs(args.input, exts))
    if not files:
        print(f"在 {args.input} 中未找到匹配扩展名 {exts} 的文件", file=sys.stderr)
        return 1

    args.output.mkdir(parents=True, exist_ok=True)

    for path in files:
        suf = path.suffix.lower()
        if suf == ".pdf":
            mode = args.mode
            if mode == "auto":
                import fitz

                doc = fitz.open(path)
                try:
                    mode = "outline" if doc.get_toc() else "compound"
                finally:
                    doc.close()

            pages = extract_pdf_pages(path)
            if args.page_markers:
                full = "\n\n".join(
                    f"--- Page {i + 1} ---\n{p}" for i, p in enumerate(pages)
                )
            else:
                full = "\n".join(pages)

            if mode == "outline":
                ch = split_pdf_by_outline(path)
                if not ch:
                    print(
                        f"{path.name}: 无 PDF 书签，改用 compound",
                        file=sys.stderr,
                    )
                    ch = split_full_text(
                        full,
                        pages,
                        mode="compound",
                        custom_pattern=args.pattern,
                        skip_first_pages=args.skip_first_pages,
                        heavy_page_threshold=args.heavy_page_threshold,
                    )
            else:
                ch = split_full_text(
                    full,
                    pages,
                    mode=mode,
                    custom_pattern=args.pattern,
                    skip_first_pages=args.skip_first_pages,
                    heavy_page_threshold=args.heavy_page_threshold,
                )
            write_book(path.stem, ch, args.output)
            print(f"完成: {path.name} -> {args.output / _slug(path.stem, 80)}")
        else:
            print(f"跳过（尚未实现）: {path.name}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
