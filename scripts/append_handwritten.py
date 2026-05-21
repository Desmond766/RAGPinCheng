"""Append the 18 hand-written items (12 multi-turn pairs + 6 no_answer) to
src/eval/golden.jsonl.

Multi-turn parent_ids are resolved by reference to existing factual /
table_formula / code_lookup / transcript items in golden.jsonl so the
grading target is always a real, indexed parent.

Idempotent: re-running won't duplicate items (matched by id).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.eval.types import EvalItem
from src.eval.io import load_jsonl, save_jsonl

GOLDEN = Path(__file__).resolve().parent.parent / "src" / "eval" / "golden.jsonl"


# Each multi-turn pair: (ref_id_for_parent, t1_question, t2_question, note_tag).
# Both turns target the *same* parent (the simpler base case — turn-2 is
# ambiguous and the rewriter must restore turn-1 context for retrieval to
# land on this parent). Cross-parent multi-turn can be added later once
# same-parent baseline numbers exist.
PAIRS = [
    ("eval-factual-0010",
     "Q345 钢手工焊连接时应选用哪个型号的焊条？",
     "Q390 呢？",
     "焊条型号承接：Q345→E50，Q390→E55，turn-2 必须靠 turn-1 才能识别"),

    ("eval-code_lookup-0003",
     "依据 6.2.1.2 表，规格 M20 普通螺栓的非全螺纹长度 l 范围是多少？",
     "M24 的呢？",
     "螺栓规格切换；turn-2 单独检索几乎不可能命中"),

    ("eval-factual-0007",
     "大六角头高强度螺栓施工中，M20/M22/M24 螺栓的初拧扭矩应控制在多少范围？",
     "对应的螺栓轴力大概是多少？",
     "扭矩→轴力承接；turn-2 缺少螺栓上下文会跑偏"),

    ("eval-factual-0001",
     "低温地区为消除冲孔和裁剪造成的局部硬化区，应采用什么工艺方法？",
     "这种处理的目的是什么？",
     "代词承接『这种处理』；turn-2 单独检索无锚点"),

    ("eval-code_lookup-0001",
     "按 GB 50011—2010（2016年版）方法算得的中柱列下柱柱间支撑斜杆截面强度应力比是多少？",
     "本书建议方法呢？",
     "GB 方法 ↔ 本书方法对比；turn-2 必须保留 turn-1 的对象"),

    ("eval-factual-0013",
     "普通 C 级螺栓的孔径 d0 相对螺栓公称直径 d 一般应大多少毫米？",
     "d≥30mm 时呢？",
     "条件分支承接；turn-2 不指明孔径就语义不全"),

    ("eval-factual-0025",
     "高层及超高层钢结构钢柱安装时柱顶标高偏差应调整到多少以内？",
     "十字线每次调整的偏差呢？",
     "同一段落不同指标承接；turn-2 不指明上下文则无意义"),

    ("eval-table_formula-0006",
     "热轧普通工字钢 I50a 的截面面积和理论重量分别是多少？",
     "I50b 的呢？",
     "工字钢型号切换；turn-2 取同一表的另一行"),

    ("eval-table_formula-0004",
     "KH 300-2 型履带式起重机在工作半径 16.0m、吊臂长度 18m 条件下的起重量约是多少？",
     "吊臂长度换成 30m 时呢？",
     "起重机表中另一列；turn-2 必须继承机型与半径"),

    ("eval-code_lookup-0006",
     "按 GB/T 4171 规定，焊接结构用高耐候性结构钢的板厚上限是多少？",
     "Q345GNHL 中 L 是什么含义？",
     "同段两个事实；turn-2 是术语解释，turn-1 给出钢号上下文"),

    ("eval-factual-0017",
     "局部退火消除应力时，板厚小于 50mm 的加热板对焊缝两侧覆盖范围是多少？",
     "板厚 ≥50mm 呢？",
     "板厚分支承接；turn-2 单独检索很可能跑到其他规范"),

    ("eval-transcript-0002",
     "在 Revit 中放置距地 300mm 的插座时，如果未隐藏面层，距地高度应输入多少？",
     "为什么不能直接输入 300？",
     "结论→原因承接；turn-2 缺少『插座/面层』上下文则无意义"),
]


# Six off-corpus questions. None of these topics is in:
#   - 钢结构基础 上册 / 钢结构设计手册 / 建筑钢结构施工手册
#   - 培训流程transcript / 插座建模transcript
# Expected behavior: system must answer "资料中未找到相关内容。"
NO_ANSWER = [
    ("幕墙玻璃在风压作用下的最大允许挠度是多少？",
     "幕墙工程，不属于钢结构主体或目前已索引的视频范围"),
    ("Revit 中创建参数化窗族时，类型参数与实例参数有什么区别？",
     "Revit 通用族操作；当前转写视频未覆盖窗族类型/实例参数"),
    ("暖通空调系统冷负荷的逐时计算方法是什么？",
     "暖通专业，超出钢结构与现有培训视频"),
    ("装配式混凝土剪力墙的现浇连接节点构造要求是什么？",
     "装配式混凝土，不属于钢结构范畴"),
    ("BIM 协同工作中工作集与中心文件的同步频率应如何设置？",
     "BIM 协同流程；现有培训视频未覆盖工作集同步频率"),
    ("钢筋混凝土框架结构的抗震等级如何根据设防类别确定？",
     "钢筋混凝土抗震，不属于钢结构设计范畴"),
]


def main() -> None:
    items = load_jsonl(GOLDEN)
    by_id = {it.id: it for it in items}
    have_ids = set(by_id.keys())

    new_items: list[EvalItem] = []

    # Multi-turn pairs — both turns target the referenced item's parent.
    for i, (ref_id, t1, t2, note) in enumerate(PAIRS, start=1):
        ref = by_id.get(ref_id)
        if ref is None:
            raise SystemExit(
                f"[append] reference id not in golden.jsonl: {ref_id}"
            )
        pid = ref.expected_parent_ids[0]
        pair_id = f"eval-multi_turn-{i:04d}"
        t1_item = EvalItem(
            id=f"{pair_id}-t1",
            kind="multi_turn",
            question=t1,
            expected_parent_ids=[pid],
            doc_type=ref.doc_type,
            category=ref.category,
            source_parent_id=pid,
            notes=f"pair {i} turn 1; ref={ref_id}",
        )
        t2_item = EvalItem(
            id=f"{pair_id}-t2",
            kind="multi_turn",
            question=t2,
            expected_parent_ids=[pid],
            doc_type=ref.doc_type,
            category=ref.category,
            source_parent_id=pid,
            notes=f"pair {i} turn 2; depends on turn 1; {note}",
        )
        new_items.append(t1_item)
        new_items.append(t2_item)

    # No-answer items — expected_parent_ids deliberately empty.
    for i, (q, why) in enumerate(NO_ANSWER, start=1):
        item = EvalItem(
            id=f"eval-no_answer-{i:04d}",
            kind="no_answer",
            question=q,
            expected_parent_ids=[],
            doc_type="",
            category="",
            source_parent_id="",
            notes=why,
        )
        new_items.append(item)

    # Idempotent merge.
    added = 0
    for it in new_items:
        if it.id in have_ids:
            continue
        items.append(it)
        have_ids.add(it.id)
        added += 1

    save_jsonl(GOLDEN, items)
    print(f"[append] +{added} items (skipped {len(new_items)-added} duplicates)")
    print(f"[append] golden.jsonl now has {len(items)} items")


if __name__ == "__main__":
    main()
