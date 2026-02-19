from diff_match_patch import diff_match_patch
import difflib
from typing import Tuple


def get_text_diff(text_a: str, text_b: str) -> Tuple[str, str, str]:
    """
    比较两个文本并返回diff

    Args:
        text_a: 旧文本
        text_b: 新文本

    Returns:
        (diff_html, diff_unified, summary)
        - diff_html: HTML格式的diff，适合展示
        - diff_unified: unified格式的diff
        - summary: 简短的变化摘要
    """
    # 使用diff-match-patch生成语义化的diff
    dmp = diff_match_patch()
    diffs = dmp.diff_main(text_a, text_b)
    dmp.diff_cleanupSemantic(diffs)

    # HTML格式
    diff_html = dmp.diff_prettyHtml(diffs)

    # Unified格式（使用difflib）
    diff_unified = "\n".join(
        difflib.unified_diff(
            text_a.splitlines(keepends=True),
            text_b.splitlines(keepends=True),
            fromfile="old_version",
            tofile="new_version",
            lineterm=""
        )
    )

    # 生成摘要
    summary = _generate_diff_summary(diffs, text_a, text_b)

    return diff_html, diff_unified, summary


def _generate_diff_summary(diffs, text_a: str, text_b: str) -> str:
    """生成diff摘要"""
    additions = 0
    deletions = 0
    unchanged = 0

    for op, text in diffs:
        length = len(text)
        if op == diff_match_patch.DIFF_INSERT:
            additions += length
        elif op == diff_match_patch.DIFF_DELETE:
            deletions += length
        elif op == diff_match_patch.DIFF_EQUAL:
            unchanged += length

    total_old = len(text_a)
    total_new = len(text_b)

    if total_old == 0:
        return f"新增内容，共{total_new}字符"

    if total_new == 0:
        return f"删除所有内容，原有{total_old}字符"

    change_ratio = (additions + deletions) / (total_old + total_new) * 100

    if change_ratio < 5:
        return f"微小变化：新增{additions}字符，删除{deletions}字符"
    elif change_ratio < 20:
        return f"中等变化：新增{additions}字符，删除{deletions}字符"
    else:
        return f"大幅变化：新增{additions}字符，删除{deletions}字符，变化率{change_ratio:.1f}%"
