import csv
import os
from collections import Counter

import openpyxl
from openpyxl.chart import BarChart, Reference
from openpyxl.formatting.rule import ColorScaleRule, DataBarRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo

# Resolve from this file so the workbook builds from a clean clone, wherever it lives.
BASE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(BASE, "combined_dataset.csv")
OUT = os.path.join(BASE, "final_dataset.xlsx")

DIFF_ORDER = ["Easy", "Medium", "Hard"]

CANON = {
    "Security": "Cybersecurity",
    "SQL": "SQL & Databases",
    "Databases & SQL": "SQL & Databases",
    "Database and SQL": "SQL & Databases",
    "Database Systems": "SQL & Databases",
    "Data Structures": "Data Structures & Algorithms",
    "Algorithms": "Data Structures & Algorithms",
    "Front-end": "Front-end Development",
    "Back-end": "Back-end Development",
    "Full-stack": "Web Development",
    "React Native": "React",
    "Cloud Computing": "SaaS & Cloud Platforms",
    "Behavioral": "Behavioral & Career",
    "Career & Behavioral": "Behavioral & Career",
    "Artificial Intelligence": "Machine Learning",
    "General Programming": "General Computer Science",
    "General Program": "General Computer Science",
    "Languages and Frameworks": "General Computer Science",
    "Emerging Technologies": "General Computer Science",
}

NAVY = "1F3864"
BLUE = "2E5C8A"
TEAL = "1F6F6B"
LIGHT = "DCE6F1"
BAND = "F2F6FB"
GREEN = "E2EFDA"
AMBER = "FFF2CC"
RED = "FCE4E4"
GREY = "F2F2F2"
DIFF_FILL = {"Easy": GREEN, "Medium": AMBER, "Hard": RED}


def canon(s):
    return CANON.get(s, s)


def load():
    rows = []
    with open(SRC, encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            rows.append([
                (r.get("prompt") or "").strip(),
                (r.get("question") or "").strip(),
                (r.get("answer") or "").strip(),
                canon((r.get("subject") or "").strip()),
                (r.get("difficulty") or "").strip(),
                "combined_dataset",
            ])
    return rows


def qkey(text):
    s = " ".join(text.lower().split())
    return s.rstrip(" ?.!:,;\"')")


def collapse(rows):
    """One row per question text; keep the most complete answer."""
    groups = {}
    order = []
    for r in rows:
        k = qkey(r[1])
        if k not in groups:
            groups[k] = []
            order.append(k)
        groups[k].append(r)
    kept = []
    removed = 0
    for k in order:
        g = groups[k]
        best = max(g, key=lambda r: (bool(r[2].strip()),
                                     r[2].strip()[-1:] in '.!?")]',
                                     len(r[2].strip())))
        kept.append(list(best) + [len(g)])
        removed += len(g) - 1
    return kept, removed


def style_header(ws, row, ncols, fill=NAVY):
    for c in range(1, ncols + 1):
        cell = ws.cell(row=row, column=c)
        cell.font = Font(bold=True, color="FFFFFF", size=11)
        cell.fill = PatternFill("solid", fgColor=fill)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = Border(bottom=Side(style="thin", color="FFFFFF"))
    ws.row_dimensions[row].height = 22


def title_band(ws, text, subtitle, ncols):
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=ncols)
    c = ws.cell(row=1, column=1, value=text)
    c.font = Font(bold=True, size=16, color="FFFFFF")
    c.fill = PatternFill("solid", fgColor=NAVY)
    c.alignment = Alignment(horizontal="left", vertical="center", indent=1)
    ws.row_dimensions[1].height = 30
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=ncols)
    s = ws.cell(row=2, column=1, value=subtitle)
    s.font = Font(italic=True, size=10, color="44546A")
    s.alignment = Alignment(horizontal="left", vertical="center", indent=1)
    ws.row_dimensions[2].height = 18


def section(ws, row, text, ncols=8, color=BLUE):
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=ncols)
    c = ws.cell(row=row, column=1, value=text)
    c.font = Font(bold=True, size=12, color="FFFFFF")
    c.fill = PatternFill("solid", fgColor=color)
    c.alignment = Alignment(horizontal="left", vertical="center", indent=1)
    ws.row_dimensions[row].height = 24
    for col in range(1, ncols + 1):
        ws.cell(row=row, column=col).fill = PatternFill("solid", fgColor=color)
        ws.cell(row=row, column=col).border = Border(
            top=Side(style="medium", color=color), bottom=Side(style="medium", color=color))


def box(ws, r1, c1, r2, c2, color="B4C6E7"):
    thin = Side(style="thin", color=color)
    for r in range(r1, r2 + 1):
        for c in range(c1, c2 + 1):
            ws.cell(row=r, column=c).border = Border(left=thin, right=thin, top=thin, bottom=thin)
    for c in range(c1, c2 + 1):
        ws.cell(row=r1, column=c).border = Border(
            left=thin, right=thin, top=Side(style="medium", color=color), bottom=thin)
        ws.cell(row=r2, column=c).border = Border(
            left=thin, right=thin, top=thin, bottom=Side(style="medium", color=color))


def num(ws, row, col, value, fill=None, bold=False, fmt=None):
    c = ws.cell(row=row, column=col, value=value)
    c.alignment = Alignment(horizontal="center")
    if fill:
        c.fill = PatternFill("solid", fgColor=fill)
    if bold:
        c.font = Font(bold=True)
    if fmt:
        c.number_format = fmt
    return c


def main():
    rows = load()
    raw_count = len(rows)
    seen = set()
    unique_rows = []
    for r in rows:
        key = tuple(r[:5])
        if key in seen:
            continue
        seen.add(key)
        unique_rows.append(r)
    dropped = raw_count - len(unique_rows)
    rows, collapsed = collapse(unique_rows)
    total = len(rows)
    subjects = Counter(r[3] for r in rows)
    diffs = Counter(r[4] for r in rows)
    combos = Counter((r[3], r[4]) for r in rows)
    diff_levels = [d for d in DIFF_ORDER if d in diffs] + sorted(d for d in diffs if d not in DIFF_ORDER)
    distinct_q = len({r[1].lower() for r in rows})
    subj_rank = subjects.most_common()
    LIMITED = "limited data (<20 questions)"
    flagged = [s for s, n in subj_rank if n < 20]
    rows = [list(r) + [LIMITED if subjects[r[3]] < 20 else ""] for r in rows]

    wb = openpyxl.Workbook()

    # ================= SUMMARY: all five analyses =================
    ws = wb.active
    ws.title = "Summary"
    NC = 8
    title_band(ws, "Interview Questions Dataset — Full Analysis",
               f"{total} unique questions from combined_dataset.csv, subjects normalised to {len(subjects)}" if not (dropped or collapsed)
               else f"{total} unique questions ({raw_count} rows loaded, {dropped} exact duplicates + {collapsed} repeated copies removed), subjects normalised to {len(subjects)}", NC)
    ws.merge_cells(start_row=3, start_column=1, end_row=3, end_column=NC)
    note = ws.cell(row=3, column=1,
                   value=(f"Primary dataset: combined_dataset.csv — one row per question. {len(flagged)} subjects hold fewer than 20 questions and are flagged 'limited data' in section 2.") if not (dropped or collapsed)
                   else f"Data cleaning: {dropped} exact duplicates + {collapsed} repeated copies removed; {len(flagged)} subjects flagged 'limited data'.")
    note.font = Font(italic=True, size=9, color="C00000")
    note.alignment = Alignment(horizontal="left", indent=1)

    # 1 ------------------------------------------------------------
    r = 4
    section(ws, r, "1.  Total Number of Questions", color=NAVY)
    labels = [("Total questions", total, LIGHT), ("Unique questions", distinct_q, LIGHT),
              ("Subjects", len(subjects), LIGHT), ("Easy", diffs.get("Easy", 0), GREEN),
              ("Medium", diffs.get("Medium", 0), AMBER), ("Hard", diffs.get("Hard", 0), RED),
              ("Duplicates removed", dropped + collapsed, "FFD7D7")]
    for i, (label, value, fill) in enumerate(labels):
        col = i + 2
        c = ws.cell(row=r + 1, column=col, value=label)
        c.font = Font(bold=True, size=9, color="44546A")
        c.alignment = Alignment(horizontal="center", wrap_text=True)
        v = ws.cell(row=r + 2, column=col, value=value)
        v.font = Font(bold=True, size=18, color=NAVY)
        v.alignment = Alignment(horizontal="center", vertical="center")
        v.fill = PatternFill("solid", fgColor=fill)
        p = ws.cell(row=r + 3, column=col, value=round(100 * value / total, 2))
        p.number_format = '0.00"%"'
        p.font = Font(size=9, color="808080")
        p.alignment = Alignment(horizontal="center")

    # 2 ------------------------------------------------------------
    r = 9
    C2 = BLUE
    section(ws, r, "2.  Questions per Subject", color=C2)
    hr = r + 1
    ws.cell(row=hr, column=1, value="#")
    ws.cell(row=hr, column=2, value="Subject")
    ws.cell(row=hr, column=3, value="Questions")
    ws.cell(row=hr, column=4, value="% of total")
    ws.cell(row=hr, column=5, value="Data flag")
    style_header(ws, hr, 5, C2)
    first = hr + 1
    for i, (s, n) in enumerate(subj_rank, 1):
        row = hr + i
        num(ws, row, 1, i, fill="EAF1FA")
        ws.cell(row=row, column=2, value=s)
        num(ws, row, 3, n, fill=LIGHT)
        num(ws, row, 4, round(100 * n / total, 2), fmt='0.00"%"')
        if n < 20:
            c = ws.cell(row=row, column=5, value=LIMITED)
            c.font = Font(bold=True, size=9, color="9C0006")
            c.fill = PatternFill("solid", fgColor="FFC7CE")
            c.alignment = Alignment(horizontal="center")
        if i % 2 == 0:
            for c in range(1, 6):
                if not ws.cell(row=row, column=c).fill.fgColor.rgb or ws.cell(row=row, column=c).fill.fgColor.rgb == "00000000":
                    ws.cell(row=row, column=c).fill = PatternFill("solid", fgColor="EAF1FA")
    last2 = hr + len(subj_rank)
    tot_row = last2 + 1
    ws.cell(row=tot_row, column=2, value="Total").font = Font(bold=True, color=NAVY)
    num(ws, tot_row, 3, total, fill="BDD7EE", bold=True)
    num(ws, tot_row, 4, 100.0, fill="BDD7EE", bold=True, fmt='0.00"%"')
    box(ws, hr, 1, tot_row, 5)
    ws.conditional_formatting.add(f"C{first}:C{last2}",
                                   DataBarRule(start_type="num", start_value=0, end_type="max", color="5B9BD5"))
    ws.conditional_formatting.add(f"D{first}:D{last2}",
                                   ColorScaleRule(start_type="min", start_color="FFFFFF", end_type="max", end_color="9DC3E6"))

    # 3 ------------------------------------------------------------
    r = tot_row + 2
    C3 = "1F6F6B"
    section(ws, r, "3.  Difficulty Level Distribution", color=C3)
    hr = r + 1
    ws.cell(row=hr, column=2, value="Difficulty")
    ws.cell(row=hr, column=3, value="Questions")
    ws.cell(row=hr, column=4, value="% of total")
    style_header(ws, hr, 4, C3)
    for i, d in enumerate(diff_levels, 1):
        row = hr + i
        ws.cell(row=row, column=2, value=d)
        num(ws, row, 3, diffs[d], fill=DIFF_FILL.get(d, GREY), bold=True)
        num(ws, row, 4, round(100 * diffs[d] / total, 2), fill=DIFF_FILL.get(d, GREY),
            bold=True, fmt='0.00"%"')
        ws.cell(row=row, column=2).fill = PatternFill("solid", fgColor=DIFF_FILL.get(d, GREY))
        ws.cell(row=row, column=2).font = Font(bold=True)
        ws.cell(row=row, column=2).alignment = Alignment(horizontal="center")
    box(ws, hr, 2, hr + len(diff_levels), 4, color="A9D08E")

    chart = BarChart()
    chart.type = "col"
    chart.title = "Questions by difficulty"
    chart.height = 7
    chart.width = 11
    chart.add_data(Reference(ws, min_col=3, min_row=hr, max_row=hr + len(diff_levels)), titles_from_data=True)
    chart.set_categories(Reference(ws, min_col=2, min_row=hr + 1, max_row=hr + len(diff_levels)))
    chart.legend = None
    ws.add_chart(chart, "E" + str(hr))

    # 4 ------------------------------------------------------------
    r = hr + len(diff_levels) + 2
    C4 = "5B4B8A"
    section(ws, r, "4.  Subject and Difficulty Distribution", color=C4)
    hr = r + 1
    ws.cell(row=hr, column=2, value="Subject")
    for j, d in enumerate(diff_levels):
        ws.cell(row=hr, column=5 + j, value=d)
    ws.cell(row=hr, column=5 + len(diff_levels), value="Total")
    style_header(ws, hr, 8, C4)
    first = hr + 1
    for i, (s, _n) in enumerate(subj_rank, 1):
        row = hr + i
        ws.cell(row=row, column=2, value=s)
        for j, d in enumerate(diff_levels):
            num(ws, row, 5 + j, combos[(s, d)])
        num(ws, row, 5 + len(diff_levels), sum(combos[(s, d)] for d in diff_levels),
            fill=LIGHT, bold=True)
        if i % 2 == 0:
            for c in range(2, 6 + len(diff_levels)):
                ws.cell(row=row, column=c).fill = PatternFill("solid", fgColor=BAND)
    last4 = hr + len(subj_rank)
    tr = last4 + 1
    ws.cell(row=tr, column=2, value="Total").font = Font(bold=True, color=NAVY)
    for j, d in enumerate(diff_levels):
        num(ws, tr, 5 + j, diffs[d], fill="D9E2F3", bold=True)
    num(ws, tr, 5 + len(diff_levels), total, fill=C4, bold=True)
    ws.cell(row=tr, column=5 + len(diff_levels)).font = Font(bold=True, color="FFFFFF")
    box(ws, hr, 2, tr, 5 + len(diff_levels), color="B4A7D6")
    ws.conditional_formatting.add(
        f"E{first}:{get_column_letter(4 + len(diff_levels))}{last4}",
        ColorScaleRule(start_type="min", start_color="FFFFFF", end_type="max", end_color="9DC3E6"))

    # 5 ------------------------------------------------------------
    r = tr + 2
    C5 = "C55A11"
    section(ws, r, "5.  Percentage Distribution", color=C5)
    hr = r + 1
    ws.cell(row=hr, column=2, value="Category")
    ws.cell(row=hr, column=3, value="Questions")
    ws.cell(row=hr, column=4, value="% of total")
    style_header(ws, hr, 4, C5)
    first = hr + 1
    row = first
    blocks = [("Subject", [(s, n) for s, n in subj_rank], "DEEBF7"),
              ("Difficulty", [(d, diffs[d]) for d in diff_levels], "FCE4D6")]
    for label, items, tint in blocks:
        for name, n in items:
            ws.cell(row=row, column=1, value=label)
            ws.cell(row=row, column=2, value=name)
            num(ws, row, 3, n, fill=tint)
            num(ws, row, 4, round(100 * n / total, 2), fmt='0.00"%"')
            ws.cell(row=row, column=1).fill = PatternFill("solid", fgColor=tint)
            ws.cell(row=row, column=1).font = Font(size=9, bold=True, color="44546A")
            row += 1
    for s, _n in subj_rank:
        for d in diff_levels:
            n = combos[(s, d)]
            if not n:
                continue
            ws.cell(row=row, column=1, value="Subject x Difficulty")
            ws.cell(row=row, column=2, value=f"{s} | {d}")
            num(ws, row, 3, n, fill="F2F2F2")
            num(ws, row, 4, round(100 * n / total, 2), fmt='0.00"%"')
            ws.cell(row=row, column=1).font = Font(size=9, color="808080")
            row += 1
    last5 = row - 1
    box(ws, hr, 1, last5, 4, color="F4B183")
    ws.conditional_formatting.add(f"D{first}:D{last5}",
                                   ColorScaleRule(start_type="min", start_color="FFFFFF",
                                                  end_type="max", end_color="F8CBAD"))

    widths = {"A": 20, "B": 40, "C": 12, "D": 12, "E": 11, "F": 12, "G": 11, "H": 12}
    for col, w in widths.items():
        ws.column_dimensions[col].width = w

    # ================= Individual sheets =================
    ws2 = wb.create_sheet("By Subject")
    title_band(ws2, "Questions per Subject", f"{len(subjects)} canonical subjects after merging synonyms", 4)
    hr = 4
    ws2.append([])
    ws2.append(["#", "Subject", "Questions", "% of total", "Data flag"])
    style_header(ws2, hr, 5)
    for i, (s, n) in enumerate(subj_rank, 1):
        row = hr + i
        num(ws2, row, 1, i)
        ws2.cell(row=row, column=2, value=s)
        num(ws2, row, 3, n)
        num(ws2, row, 4, round(100 * n / total, 2), fmt='0.00"%"')
        if n < 20:
            c = ws2.cell(row=row, column=5, value=LIMITED)
            c.font = Font(bold=True, size=9, color="9C0006")
            c.fill = PatternFill("solid", fgColor="FFC7CE")
            c.alignment = Alignment(horizontal="center")
        if i % 2 == 0:
            for c in range(1, 6):
                if not ws2.cell(row=row, column=c).fill.fgColor.rgb or ws2.cell(row=row, column=c).fill.fgColor.rgb == "00000000":
                    ws2.cell(row=row, column=c).fill = PatternFill("solid", fgColor=BAND)
    last = hr + len(subj_rank)
    ws2.cell(row=last + 1, column=2, value="Total").font = Font(bold=True)
    num(ws2, last + 1, 3, total, fill=LIGHT, bold=True)
    num(ws2, last + 1, 4, 100.0, fill=LIGHT, bold=True, fmt='0.00"%"')
    for c, w in {"A": 6, "B": 42, "C": 13, "D": 12, "E": 26}.items():
        ws2.column_dimensions[c].width = w
    ws2.conditional_formatting.add(f"C{hr+1}:C{last}",
                                   DataBarRule(start_type="num", start_value=0, end_type="max", color="5B9BD5"))
    ws2.conditional_formatting.add(f"D{hr+1}:D{last}",
                                   ColorScaleRule(start_type="min", start_color="FFFFFF", end_type="max", end_color="9DC3E6"))
    ws2.freeze_panes = f"A{hr+1}"

    ws3 = wb.create_sheet("By Difficulty")
    title_band(ws3, "Difficulty Level Distribution", "Easy / Medium / Hard breakdown with share of the dataset", 3)
    ws3.append([])
    ws3.append([])
    hr = 4
    ws3.append(["Difficulty", "Questions", "% of total"])
    style_header(ws3, hr, 3)
    for i, d in enumerate(diff_levels, 1):
        row = hr + i
        ws3.cell(row=row, column=1, value=d)
        num(ws3, row, 2, diffs[d], fill=DIFF_FILL.get(d, GREY))
        num(ws3, row, 3, round(100 * diffs[d] / total, 2), fill=DIFF_FILL.get(d, GREY), fmt='0.00"%"')
        ws3.cell(row=row, column=1).fill = PatternFill("solid", fgColor=DIFF_FILL.get(d, GREY))
    for c, w in {"A": 14, "B": 12, "C": 12}.items():
        ws3.column_dimensions[c].width = w
    ws3.conditional_formatting.add(f"B{hr+1}:B{hr+len(diff_levels)}",
                                   DataBarRule(start_type="num", start_value=0, end_type="max", color="70AD47"))

    ws4 = wb.create_sheet("Subject x Difficulty")
    title_band(ws4, "Subject and Difficulty Distribution", "Question count for every subject and difficulty combination", 5)
    hr = 4
    ws4.append([])
    ws4.append(["Subject"] + diff_levels + ["Total"])
    style_header(ws4, hr, 2 + len(diff_levels))
    for i, (s, _n) in enumerate(subj_rank, 1):
        row = hr + i
        ws4.cell(row=row, column=1, value=s)
        for j, d in enumerate(diff_levels, 2):
            num(ws4, row, j, combos[(s, d)])
        num(ws4, row, 2 + len(diff_levels), sum(combos[(s, d)] for d in diff_levels), bold=True)
        if i % 2 == 0:
            for c in range(1, 3 + len(diff_levels)):
                ws4.cell(row=row, column=c).fill = PatternFill("solid", fgColor=BAND)
    tr = hr + len(subj_rank) + 1
    ws4.cell(row=tr, column=1, value="Total").font = Font(bold=True)
    for j, d in enumerate(diff_levels, 2):
        num(ws4, tr, j, diffs[d], fill=LIGHT, bold=True)
    c = num(ws4, tr, 2 + len(diff_levels), total, fill=NAVY, bold=True)
    c.font = Font(bold=True, color="FFFFFF")
    ws4.column_dimensions["A"].width = 40
    for j in range(2, 3 + len(diff_levels)):
        ws4.column_dimensions[get_column_letter(j)].width = 12
    ws4.conditional_formatting.add(
        f"B{hr+1}:{get_column_letter(1 + len(diff_levels))}{hr+len(subj_rank)}",
        ColorScaleRule(start_type="min", start_color="FFFFFF", end_type="max", end_color="9DC3E6"))
    ws4.freeze_panes = f"B{hr+1}"

    # ---------- Coverage matrix ----------
    wsc = wb.create_sheet("Coverage Matrix")
    missing_total = sum(1 for s, _n in subj_rank for d in diff_levels if not combos[(s, d)])
    title_band(wsc, "Coverage Matrix",
               f"{len(subj_rank) * len(diff_levels)} subject x difficulty combinations, {missing_total} missing"
               f" ({len(subj_rank)} subjects x Easy/Medium/Hard)", 6)
    hr = 4
    wsc.append([])
    wsc.append(["Subject", "Easy", "Medium", "Hard", "Questions", "Coverage"])
    style_header(wsc, hr, 6, "1F6F6B")
    for i, (s, n) in enumerate(subj_rank, 1):
        row = hr + i
        wsc.cell(row=row, column=1, value=s)
        covered = 0
        for j, d in enumerate(diff_levels, 2):
            has = combos[(s, d)] > 0
            covered += 1 if has else 0
            c = wsc.cell(row=row, column=j, value="Available" if has else "Missing")
            c.alignment = Alignment(horizontal="center")
            if has:
                c.fill = PatternFill("solid", fgColor=GREEN)
                c.font = Font(color="006100", bold=True, size=10)
            else:
                c.fill = PatternFill("solid", fgColor="FFC7CE")
                c.font = Font(color="9C0006", bold=True, size=10)
        num(wsc, row, 5, n, fill=LIGHT)
        cov = f"{covered}/{len(diff_levels)}"
        cc = wsc.cell(row=row, column=6, value=cov)
        cc.alignment = Alignment(horizontal="center")
        if covered == len(diff_levels):
            cc.fill = PatternFill("solid", fgColor=GREEN)
            cc.font = Font(color="006100", bold=True, size=10)
        else:
            cc.fill = PatternFill("solid", fgColor="FFEB9C")
            cc.font = Font(color="9C6500", bold=True, size=10)
    lastc = hr + len(subj_rank)
    tr_c = lastc + 1
    wsc.cell(row=tr_c, column=1, value="Total").font = Font(bold=True)
    for j, d in enumerate(diff_levels, 2):
        num(wsc, tr_c, j, diffs[d], fill="D9E2F3", bold=True)
    num(wsc, tr_c, 5, total, fill=NAVY, bold=True)
    wsc.cell(row=tr_c, column=5).font = Font(bold=True, color="FFFFFF")
    num(wsc, tr_c, 6, f"{len(subj_rank) * len(diff_levels) - missing_total}/{len(subj_rank) * len(diff_levels)}",
        fill=NAVY, bold=True)
    wsc.cell(row=tr_c, column=6).font = Font(bold=True, color="FFFFFF")
    box(wsc, hr, 1, tr_c, 6, color="A9D08E")
    wsc.column_dimensions["A"].width = 34
    for c in ("B", "C", "D"):
        wsc.column_dimensions[c].width = 13
    wsc.column_dimensions["E"].width = 12
    wsc.column_dimensions["F"].width = 12
    wsc.freeze_panes = f"B{hr+1}"

    # counts block underneath
    cr = tr_c + 3
    wsc.merge_cells(start_row=cr, start_column=1, end_row=cr, end_column=6)
    h = wsc.cell(row=cr, column=1, value="Question counts per combination")
    h.font = Font(bold=True, size=11, color="FFFFFF")
    h.fill = PatternFill("solid", fgColor="1F6F6B")
    h.alignment = Alignment(horizontal="left", indent=1)
    hdr2 = cr + 1
    wsc.cell(row=hdr2, column=1, value="Subject")
    for j, d in enumerate(diff_levels, 2):
        wsc.cell(row=hdr2, column=j, value=d)
    wsc.cell(row=hdr2, column=5, value="Total")
    style_header(wsc, hdr2, 5, "1F6F6B")
    for i, (s, n) in enumerate(subj_rank, 1):
        row = hdr2 + i
        wsc.cell(row=row, column=1, value=s)
        for j, d in enumerate(diff_levels, 2):
            num(wsc, row, j, combos[(s, d)])
        num(wsc, row, 5, n, fill=LIGHT)
    last2c = hdr2 + len(subj_rank)
    box(wsc, hdr2, 1, last2c, 5, color="A9D08E")
    wsc.conditional_formatting.add(
        f"B{hdr2+1}:D{last2c}",
        ColorScaleRule(start_type="min", start_color="FFFFFF", end_type="max", end_color="9DC3E6"))

    ws5 = wb.create_sheet("Percentages")
    title_band(ws5, "Percentage Distribution", "Share of the total dataset held by each subject, difficulty and combination", 4)
    hr = 4
    ws5.append([])
    ws5.append(["Breakdown", "Category", "Questions", "% of total"])
    style_header(ws5, hr, 4)
    r = hr + 1
    for label, items, _tint in blocks:
        for name, n in items:
            ws5.cell(row=r, column=1, value=label)
            ws5.cell(row=r, column=2, value=name)
            num(ws5, r, 3, n)
            num(ws5, r, 4, round(100 * n / total, 2), fmt='0.00"%"')
            if r % 2 == 0:
                for c in range(1, 5):
                    ws5.cell(row=r, column=c).fill = PatternFill("solid", fgColor=BAND)
            r += 1
    for s, _n in subj_rank:
        for d in diff_levels:
            n = combos[(s, d)]
            if not n:
                continue
            ws5.cell(row=r, column=1, value="Subject x Difficulty")
            ws5.cell(row=r, column=2, value=f"{s} | {d}")
            num(ws5, r, 3, n)
            num(ws5, r, 4, round(100 * n / total, 2), fmt='0.00"%"')
            r += 1
    for c, w in {"A": 22, "B": 46, "C": 12, "D": 12}.items():
        ws5.column_dimensions[c].width = w
    ws5.conditional_formatting.add(f"D{hr+1}:D{r-1}",
                                   ColorScaleRule(start_type="min", start_color="FFFFFF",
                                                  end_type="max", end_color="F8CBAD"))
    ws5.freeze_panes = f"A{hr+1}"

    wsd = wb.create_sheet("All Questions")
    title_band(wsd, "All Questions",
               f"{total} unique questions — prompt | question | answer | subject | difficulty | source | copies_found | data_flag", 8)
    hr = 4
    wsd.append([])
    wsd.append(["prompt", "question", "answer", "subject", "difficulty", "source", "copies_found", "data_flag"])
    style_header(wsd, hr, 8)
    for row in rows:
        wsd.append(row)
    last = hr + total
    for c, w in {"A": 52, "B": 52, "C": 95, "D": 28, "E": 11, "F": 20, "G": 13, "H": 26}.items():
        wsd.column_dimensions[c].width = w
    wsd.freeze_panes = f"A{hr+1}"
    tab = Table(displayName="Questions", ref=f"A{hr}:H{last}")
    tab.tableStyleInfo = TableStyleInfo(name="TableStyleMedium2", showRowStripes=True,
                                        showColumnStripes=False)
    wsd.add_table(tab)

    saved = None
    for name in ("final_dataset.xlsx", "final_dataset_v2.xlsx", "final_dataset_v3.xlsx"):
        target = os.path.join(BASE, name)
        try:
            wb.save(target)
            saved = target
            if name != "final_dataset.xlsx":
                print("NOTE: final_dataset.xlsx is open in Excel, saved to", target)
            break
        except PermissionError:
            print("locked, trying next name:", name)
    if saved is None:
        raise SystemExit("all target files are locked; close Excel and rerun")
    print("limited subjects:", flagged)
    print("rows:", total, "| exact dupes dropped:", dropped, "| repeats collapsed:", collapsed)
    print("subjects:", len(subjects))
    print("summary sections end at row:", last5)
    print("saved:", saved, os.path.getsize(saved), "bytes")


if __name__ == "__main__":
    main()
