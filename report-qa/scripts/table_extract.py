"""Conservative table extraction for text-based Chinese annual-report PDFs.

The normal ruled-grid extractor is preferred. Two common annual-report layouts
need repair: a shaded header above horizontal rules, and a shaded current-year
column with otherwise unruled cells. Repairs use existing PDF geometry, never
financial values. Whole words are assigned to cells so a number is not sliced
at an inferred boundary. All physical rows (including repeated headers) survive
in rawRows; qualityFlags distinguish inferred geometry from native grids.
"""
from __future__ import annotations

from bisect import bisect_right
from collections import Counter
import re


NUMBER = re.compile(r"^[（(]?[-−–+]?\d[\d,，]*(?:\.\d+)?[%％]?[)）]?$" )
YEAR = re.compile(r"(?:19|20)\d{2}(?:\s*年)?")


def _clusters(values, tolerance=2.0):
    groups = []
    for value in sorted(values):
        if groups and value - sum(groups[-1]) / len(groups[-1]) <= tolerance:
            groups[-1].append(value)
        else:
            groups.append([value])
    return groups


def _unique(values, tolerance=2.0):
    return [sum(group) / len(group) for group in _clusters(values, tolerance)]


def _header(row):
    cells = [str(value or "").strip() for value in row]
    return (sum(bool(YEAR.search(value)) for value in cells) >= 2
            or sum(bool(re.search(r"第[一二三四1-4]季度", value)) for value in cells) >= 2
            or any(re.search(r"^(?:项\s*目|指标|科目|名称|资产|负债和所有者权益)$", value)
                   for value in cells))


def _package(bbox, raw_rows, method, flags=()):
    raw = [[None if value is None else str(value).strip() for value in row]
           for row in raw_rows]
    raw = [row for row in raw if any(value for value in row)]
    if len(raw) < 2:
        return None
    width = max(map(len, raw))
    if width < 2:
        return None
    # Padding records empty cells; it does not impute their contents.
    raw = [row + [None] * (width - len(row)) for row in raw]
    flags = list(flags)
    if _header(raw[0]):
        headers, rows = raw[0], raw[1:]
        # Preserve both raw header levels, but make paired subcolumns meaningful
        # for consumers (e.g. each year's 期末 / 平均). No numeric data row is used.
        if len(raw) > 2 and sum(str(v or "").strip() in ("期末", "平均") for v in raw[1]) >= 4:
            headers = []
            parent = ""
            for upper, lower in zip(raw[0], raw[1]):
                if upper:
                    parent = upper
                if str(lower or "").strip() in ("期末", "平均") and YEAR.search(parent):
                    headers.append(parent + " " + lower)
                else:
                    headers.append(lower or upper or "")
            rows = raw[2:]
            flags.append("paired_year_subheaders_combined_raw_rows_preserved")
    else:
        headers, rows = [f"列 {index + 1}" for index in range(width)], raw
        flags.append("header_not_detected")
    if any(_header(row) for row in raw[1:]):
        flags.append("internal_header_rows_preserved")
    if any(value is None for row in raw for value in row):
        flags.append("empty_or_merged_cells")
    return {"bbox": [round(float(v), 3) for v in bbox], "headers": headers,
            "rows": rows, "rawRows": raw, "method": method,
            "qualityFlags": sorted(set(flags))}


def _horizontal_groups(page):
    """Merge adjacent horizontal segments; retain their original endpoints."""
    horizontal = [edge for edge in page.edges if edge.get("orientation") == "h"
                  and edge["x1"] - edge["x0"] > 2]
    levels = []
    for edge in sorted(horizontal, key=lambda item: item["top"]):
        if levels and abs(edge["top"] - levels[-1][0]["top"]) <= 1.2:
            levels[-1].append(edge)
        else:
            levels.append([edge])
    result = []
    for edges in levels:
        y = sum(edge["top"] for edge in edges) / len(edges)
        spans = []
        for edge in sorted(edges, key=lambda item: item["x0"]):
            if spans and edge["x0"] <= spans[-1]["x1"] + 3:
                spans[-1]["x1"] = max(spans[-1]["x1"], edge["x1"])
                spans[-1]["endpoints"].extend([edge["x0"], edge["x1"]])
            else:
                spans.append({"x0": edge["x0"], "x1": edge["x1"],
                              "endpoints": [edge["x0"], edge["x1"]]})
        for span in spans:
            result.append({**span, "y": y})
    return result


def _join_words(words):
    if not words:
        return ""
    lines = []
    for word in sorted(words, key=lambda item: (item["top"], item["x0"])):
        center = (word["top"] + word["bottom"]) / 2
        if lines and abs(center - lines[-1][0]) <= 3:
            lines[-1][1].append(word)
        else:
            lines.append((center, [word]))
    output = []
    for _, line in lines:
        line.sort(key=lambda item: item["x0"])
        text = line[0]["text"]
        for previous, current in zip(line, line[1:]):
            # Do not join separate numbers into an invented larger number.
            gap = current["x0"] - previous["x1"]
            separate = gap > 1 or (NUMBER.fullmatch(previous["text"])
                                   and NUMBER.fullmatch(current["text"]))
            text += (" " if separate else "") + current["text"]
        output.append(text)
    return "\n".join(output)


def _word_grid(words, xs, ys):
    cells = [[[] for _ in xs[:-1]] for _ in ys[:-1]]
    flags = []
    for word in words:
        cx, cy = (word["x0"] + word["x1"]) / 2, (word["top"] + word["bottom"]) / 2
        if not (xs[0] <= cx <= xs[-1] and ys[0] <= cy < ys[-1]):
            continue
        column = min(len(xs) - 2, max(0, bisect_right(xs, cx) - 1))
        row = min(len(ys) - 2, max(0, bisect_right(ys, cy) - 1))
        cells[row][column].append(word)
        if word["x0"] < xs[column] - 2 or word["x1"] > xs[column + 1] + 2:
            flags.append("word_crosses_column_boundary_preserved_whole")
    return [[_join_words(cell) for cell in row] for row in cells], flags


def _repair_native_grid(table, words):
    """Remove ornamental cell splits only when PDF words prove them spurious.

    Some PDFs draw small boxes around header text, creating 15 apparent columns
    over a five-column body. Others put a grid boundary through a complete
    numeric word. The dominant body cell geometry and unsplit PDF words are
    stronger evidence than pdfplumber's global cell union in these cases.
    """
    left, top, right, bottom = table.bbox
    xs = _unique([v for cell in table.cells for v in (cell[0], cell[2])], 1)
    if len(xs) < 3:
        return None
    patterns = Counter(tuple(round(v, 1) for v in _unique(
        [v for cell in row.cells if cell for v in (cell[0], cell[2])], 1))
        for row in table.rows)
    dominant, count = patterns.most_common(1)[0]
    flags = []
    if count >= max(3, len(table.rows) * 0.55) and 3 <= len(dominant) < len(xs):
        # Outer label / last-year cells may span several rows and therefore be
        # absent from row.cells; retaining the table envelope prevents data loss.
        xs = _unique([left, *dominant, right], 1)
        flags.append("ornamental_header_splits_replaced_by_dominant_body_columns")
    numeric_crossing = any(
        NUMBER.fullmatch(word["text"]) and top <= word["top"] < bottom
        and left <= word["x0"] and word["x1"] <= right + 1
        and any(word["x0"] < x - 1 and word["x1"] > x + 1 for x in xs[1:-1])
        for word in words)
    if numeric_crossing:
        flags.append("native_boundary_crossed_numeric_word_repaired")
    if not flags:
        return None
    ys = _unique([v for cell in table.cells for v in (cell[1], cell[3])], 1)
    raw, word_flags = _word_grid(words, xs, ys)
    keep = [index for index in range(len(xs) - 1) if any(row[index] for row in raw)]
    if len(keep) < len(xs) - 1:
        raw = [[row[index] for index in keep] for row in raw]
        flags.append("empty_spurious_columns_removed_after_whole_word_assignment")
    return _package(table.bbox, raw, "ruled_grid_repaired_with_whole_pdf_words",
                    [*flags, *word_flags])


def _extend_header(table, rules, words):
    left, top, right, bottom = table.bbox
    xs = _unique([value for cell in table.cells for value in (cell[0], cell[2])])
    if len(xs) < 3 or right - left < 160 or bottom - top > 65:
        return None
    matching = [rule for rule in rules if abs(rule["x0"] - left) < 3
                and abs(rule["x1"] - right) < 3 and rule["y"] >= top - 3]
    matching.sort(key=lambda rule: rule["y"])
    ys = _unique([cell[1] for cell in table.cells] + [bottom], 1.5)
    last = bottom
    additions = []
    for rule in matching:
        y = rule["y"]
        if y <= bottom + 2:
            continue
        if y - last > 40:
            break
        additions.append(y)
        last = y
    if len(additions) < 2:
        return None
    ys = _unique(ys + additions, 1.5)
    raw, flags = _word_grid(words, xs, ys)
    return _package([xs[0], ys[0], xs[-1], ys[-1]], raw,
                    "header_columns_extended_along_horizontal_rules",
                    ["vertical_boundaries_extended_from_header", *flags])


def _aligned_numeric_columns(words, bounds, minimum_support=3):
    left, top, right, bottom = bounds
    numeric = [word for word in words if NUMBER.fullmatch(word["text"])
               and left <= word["x0"] and word["x1"] <= right + 1
               and top <= word["top"] < bottom]
    clusters = _clusters([word["x1"] for word in numeric], 2.5)
    ends = [sum(group) / len(group) for group in clusters if len(group) >= minimum_support]
    if len(ends) < 3 or len(ends) > 12:
        return None
    starts = [min(word["x0"] for word in numeric if abs(word["x1"] - end) <= 2.5)
              for end in ends]
    if any(start <= ends[index - 1] + 2 for index, start in enumerate(starts) if index):
        return None
    first = max(left + 45, starts[0] - 8)
    xs = [left, first]
    xs += [(ends[index - 1] + starts[index]) / 2 for index in range(1, len(ends))]
    return xs + [right]


def _expand_shaded_column(table, rules, words):
    left, top, right, bottom = table.bbox
    columns = _unique([value for cell in table.cells for value in (cell[0], cell[2])])
    if len(columns) not in (2, 3) or len(table.rows) < 2:
        return None
    wide = [rule for rule in rules if top - 3 <= rule["y"] <= bottom + 3
            and rule["x0"] <= left + 2 and rule["x1"] >= right - 2
            and rule["x1"] - rule["x0"] > (right - left) * 2.5]
    if not wide:
        return None
    # A label cell can span several rows while separators run only under the
    # numeric portion. Keep those partial rules inside the full-width envelope.
    outer_left = min(rule["x0"] for rule in wide)
    outer_right = max(rule["x1"] for rule in wide)
    wide = [rule for rule in wide if abs(rule["x1"] - outer_right) < 3]
    endpoint_groups = _clusters([point for rule in wide for point in set(rule["endpoints"])], 2)
    candidates = [sum(group) / len(group) for group in endpoint_groups]
    xs = []
    for point in candidates:
        support = sum(any(abs(endpoint - point) <= 2 for endpoint in rule["endpoints"])
                      for rule in wide)
        if (support >= min(2, len(wide)) or abs(point - outer_left) < 2
                or abs(point - outer_right) < 2):
            xs.append(point)
    flags = ["row_boundaries_extended_from_shaded_column"]
    if len(wide) == 1:
        flags.append("column_layout_supported_by_one_full_width_rule")
    if len(xs) < 4:
        xs = _aligned_numeric_columns(words, [outer_left, top, outer_right, bottom])
        flags.append("columns_inferred_from_repeated_numeric_alignment")
    else:
        flags.append("columns_recovered_from_horizontal_segment_endpoints")
    if not xs or len(xs) > 14:
        return None
    ys = _unique([value for cell in table.cells for value in (cell[1], cell[3])], 1)
    ys[-1] = max(ys[-1], max(rule["y"] for rule in wide))
    # The common CMB layout prints the real years immediately above its first rule.
    above = [word for word in words if top - 36 <= word["top"] < top
             and word["bottom"] <= top + 1
             and outer_left <= (word["x0"] + word["x1"]) / 2 <= outer_right]
    if sum(bool(YEAR.fullmatch(word["text"])) for word in above) >= 2:
        ys.insert(0, min(word["top"] for word in above) - 0.5)
        flags.append("header_recovered_above_grid")
    raw, word_flags = _word_grid(words, xs, ys)
    return _package([xs[0], ys[0], xs[-1], ys[-1]], raw,
                    "shaded_column_expanded_using_pdf_geometry", [*flags, *word_flags])


def _overlap(first, second):
    a, b = first["bbox"], second["bbox"]
    area = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    small = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return area / small if small else 0


def _text_aligned_fallback(page, words, existing):
    """Conservative fallback for several dense numeric rows, never page-wide text strategy.

    This preserves physical baselines and explicitly flags unmerged wrapped labels.
    Prose containing a few amounts does not qualify: at least four nearby rows with
    three repeated numeric columns and a short nonnumeric label are required.
    """
    lines = []
    for word in sorted(words, key=lambda item: (item["top"], item["x0"])):
        cy = (word["top"] + word["bottom"]) / 2
        if lines and abs(cy - lines[-1][0]) <= 3:
            lines[-1][1].append(word)
        else:
            lines.append((cy, [word]))
    dense = []
    for cy, line in lines:
        numeric = [word for word in line if NUMBER.fullmatch(word["text"])]
        labels = [word for word in line if not NUMBER.fullmatch(word["text"])]
        if len(numeric) >= 3 and labels and sum(len(word["text"]) for word in labels) <= 65:
            if not any(box["bbox"][1] - 2 <= cy <= box["bbox"][3] + 2 for box in existing):
                dense.append((cy, line))
    groups = []
    for line in dense:
        if groups and line[0] - groups[-1][-1][0] <= 36:
            groups[-1].append(line)
        else:
            groups.append([line])
    result = []
    for group in groups:
        if len(group) < 4:
            continue
        region_words = [word for _, line in group for word in line]
        left, right = min(word["x0"] for word in region_words), max(word["x1"] for word in region_words)
        top, bottom = min(word["top"] for word in region_words), max(word["bottom"] for word in region_words)
        xs = _aligned_numeric_columns(region_words, [left, top, right, bottom], 3)
        if not xs:
            continue
        # All physical lines in the span are kept, including wrapped labels and footnotes.
        physical = [(cy, line) for cy, line in lines if top <= cy <= bottom]
        ys = [top - 0.5] + [(a[0] + b[0]) / 2 for a, b in zip(physical, physical[1:])] + [bottom + 0.5]
        # A shaded header may be the only ruled portion of a borderless table.
        # Join it only when it is immediately above and spans the same columns.
        nearby_headers = [table for table in existing
                          if 0 <= top - table["bbox"][3] <= 28
                          and table["bbox"][2] - table["bbox"][0] >= (right - left) * .9
                          and _header(table["rawRows"][0])]
        if nearby_headers:
            header = max(nearby_headers, key=lambda table: table["bbox"][3])
            xs[0], xs[-1] = min(xs[0], header["bbox"][0]), max(xs[-1], header["bbox"][2])
            ys.insert(0, header["bbox"][1])
        raw, flags = _word_grid(words, xs, ys)
        candidate = _package([xs[0], ys[0], xs[-1], ys[-1]], raw,
                             "conservative_repeated_numeric_alignment",
                             ["columns_inferred_from_repeated_numeric_alignment",
                              "rows_are_physical_text_lines_wrapped_labels_not_merged", *flags])
        if candidate:
            result.append(candidate)
    return result


def extract_tables(page):
    """Return serializable table records for one pdfplumber Page; never modify it."""
    native = page.find_tables()
    words = page.extract_words(x_tolerance=2, y_tolerance=2)
    candidates = []
    repair_seeds = []
    for table in native:
        raw = table.extract(x_tolerance=2, y_tolerance=2)
        packaged = (_repair_native_grid(table, words)
                    or _package(table.bbox, raw, "pdfplumber_ruled_grid"))
        if packaged:
            candidates.append(packaged)
        column_count = max((len(row) for row in raw), default=0)
        if column_count <= 2 or len(raw) <= 3:
            repair_seeds.append(table)
    # extract_words is linear in page characters; avoid the expensive text-table
    # search, which generates many intersections for ordinary narrative pages.
    if repair_seeds:
        rules = _horizontal_groups(page)
        for table in repair_seeds:
            candidate = (_expand_shaded_column(table, rules, words)
                         or _extend_header(table, rules, words))
            if candidate:
                candidates.append(candidate)
    # Prefer the most complete representation; a recovered full table subsumes
    # its shaded category headers and the native one-column fragment.
    candidates.sort(key=lambda item: ((item["bbox"][2] - item["bbox"][0]) *
                                     (item["bbox"][3] - item["bbox"][1]),
                                     len(item["rawRows"])), reverse=True)
    selected = []
    for candidate in candidates:
        if not any(_overlap(candidate, prior) > 0.90 for prior in selected):
            selected.append(candidate)
    if sum(bool(NUMBER.fullmatch(word["text"])) for word in words) >= 12:
        selected.extend(_text_aligned_fallback(page, words, selected))
    selected.sort(key=lambda item: ((item["bbox"][2] - item["bbox"][0]) *
                                  (item["bbox"][3] - item["bbox"][1])), reverse=True)
    deduplicated = []
    for candidate in selected:
        if not any(_overlap(candidate, prior) > .9 for prior in deduplicated):
            deduplicated.append(candidate)
    return sorted(deduplicated, key=lambda item: (item["bbox"][1], item["bbox"][0]))
