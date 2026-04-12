#!/usr/bin/env python3
"""
测试报告汇总脚本

扫描所有测试报告，生成汇总报告。
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


def parse_report(filepath: Path) -> dict[str, Any] | None:
    """Parse a single markdown test report."""
    try:
        content = filepath.read_text(encoding="utf-8")
    except Exception:
        return None

    result = {
        "filename": filepath.name,
        "timestamp": filepath.stat().st_mtime,
        "status": "unknown",
        "preset": "unknown",
        "duration": 0.0,
        "scores": {},
    }

    # Parse status
    for line in content.split("\n"):
        if line.startswith("**状态**:"):
            if "PASSED" in line or "✅" in line:
                result["status"] = "passed"
            elif "FAILED" in line or "❌" in line:
                result["status"] = "failed"
            elif "TIMEOUT" in line or "⏱️" in line:
                result["status"] = "timeout"
        elif line.startswith("**模板**:"):
            # Extract preset name
            parts = line.split("`")
            if len(parts) >= 2:
                result["preset"] = parts[1]
        elif line.startswith("| 总耗时 |"):
            parts = line.split("|")
            if len(parts) >= 3:
                try:
                    result["duration"] = float(parts[2].strip().replace(" 秒", ""))
                except ValueError:
                    pass
        elif line.startswith("| 步行性 |") or line.startswith("| **综合** |"):
            parts = line.split("|")
            if len(parts) >= 3:
                try:
                    score = parts[2].strip().replace("**", "")
                    result["scores"]["overall"] = int(score)
                except ValueError:
                    pass

    return result


def generate_summary_report(reports_dir: Path, output_path: Path) -> None:
    """Generate a summary report from all test reports."""

    # Find all test reports
    report_files = sorted(
        reports_dir.glob("test_*.md"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    if not report_files:
        print(f"未找到测试报告: {reports_dir}")
        sys.exit(1)

    # Parse reports
    results = []
    for filepath in report_files:
        parsed = parse_report(filepath)
        if parsed:
            results.append(parsed)

    # Calculate statistics
    total = len(results)
    passed = sum(1 for r in results if r["status"] == "passed")
    failed = sum(1 for r in results if r["status"] == "failed")
    timeout = sum(1 for r in results if r["status"] == "timeout")

    avg_duration = sum(r["duration"] for r in results) / total if total > 0 else 0
    scores = [r["scores"].get("overall", 0) for r in results if r["scores"].get("overall")]
    avg_score = sum(scores) / len(scores) if scores else 0

    # Build summary
    lines = [
        "# Workbench 测试汇总报告",
        "",
        f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"**报告目录**: `{reports_dir}`",
        "",
        "## 统计摘要",
        "",
        "| 指标 | 值 |",
        "|------|-----|",
        f"| 总测试数 | {total} |",
        f"| ✅ 通过 | {passed} |",
        f"| ❌ 失败 | {failed} |",
        f"| ⏱️ 超时 | {timeout} |",
        f"| 通过率 | {passed/total*100:.1f}% |" if total > 0 else "| 通过率 | N/A |",
        f"| 平均耗时 | {avg_duration:.1f} 秒 |",
        f"| 平均评分 | {avg_score:.1f} |",
        "",
        "## 最近测试",
        "",
        "| 时间 | 模板 | 状态 | 耗时 | 综合评分 |",
        "|------|------|------|------|----------|",
    ]

    for r in results[:10]:  # Show last 10
        timestamp = datetime.fromtimestamp(r["timestamp"]).strftime("%m-%d %H:%M")
        status_emoji = {"passed": "✅", "failed": "❌", "timeout": "⏱️"}.get(r["status"], "?")
        score = r["scores"].get("overall", "N/A")
        lines.append(f"| {timestamp} | {r['preset']} | {status_emoji} | {r['duration']:.0f}s | {score} |")

    lines.extend(["", "## 所有测试报告", ""])

    for r in results:
        timestamp = datetime.fromtimestamp(r["timestamp"]).strftime("%Y-%m-%d %H:%M:%S")
        status_emoji = {"passed": "✅", "failed": "❌", "timeout": "⏱️"}.get(r["status"], "?")
        lines.append(f"- {timestamp} [{status_emoji} {r['status']}] {r['preset']} - [`{r['filename']}`]({r['filename']})")

    lines.extend(["", "---", "", "*由 test_pipeline.py 自动生成*"])

    # Write summary
    output_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"汇总报告已生成: {output_path}")


def main():
    reports_dir = Path("artifacts/test_reports")
    output_path = reports_dir / "SUMMARY.md"

    generate_summary_report(reports_dir, output_path)


if __name__ == "__main__":
    main()
