"""Three in-place engine changes measured for the scaling plan, as text patches to a COPY of engine.py.

Nothing in backend/ is modified by these scripts. Each change is meant to give exactly the
same results as the engine does today (see docs/performance-scaling-plan.md, Phase 1):

2  "Must also match" columns are tested on the key's own exact candidates, with the same test
   the index applies, instead of first collecting every destination row that shares the value.
3  A compared field whose two values are identical as read is not sent through type detection
   and comparison (every comparison type answers "equal, 100%" for identical values).
4  The similar-key fallback (rows whose key has no exact partner) no longer builds the full list
   of destination rows that pass the "must also match" columns. The set per distinct value is
   computed once and reused, and counting stops as soon as more than 25 are found.

If engine.py changes, the text being replaced may no longer be found; the assertion in
``_swap`` then says which patch needs updating.
"""
from __future__ import annotations

from pathlib import Path

PRELUDE = '''
from app.reconciliation_engine.cache import compact_identifier as _qw_compact, tokens as _qw_tokens


def _passes_like_index(source_row: dict, destination_row: dict, conditions: list[dict]) -> bool:
    """The test IndexedCandidateMatcher.find_secondary_candidates applies, for one pair of rows."""
    normalizer = active_normalizer()
    for condition in conditions:
        source_value = source_row.get(condition["source_column"])
        destination_value = destination_row.get(condition["destination_column"])
        method = str(condition.get("comparison_method", ""))
        if method == "exact_text":
            identity = normalizer.identity(source_value)
            if not identity or identity != normalizer.identity(destination_value):
                return False
        elif method == "normalized_date":
            left, right = parse_date_value(source_value), parse_date_value(destination_value)
            if not left or not right or left.isoformat() != right.isoformat():
                return False
        elif method == "numeric_tolerance":
            number, other = to_number(source_value), to_number(destination_value)
            tolerance = float(condition.get("numeric_tolerance") or 0)
            if number is None or other is None or not (number - tolerance <= other <= number + tolerance):
                return False
        elif method == "matcher_based":
            destination_tokens = set(_qw_tokens(destination_value, use_synonyms=True))
            if not any(token in destination_tokens for token in _qw_tokens(source_value, use_synonyms=True)):
                return False
            if not compare_values(source_value, destination_value, "", condition["destination_column"]).matched:
                return False
        else:
            return False
    return True


class _FastMatcher(IndexedCandidateMatcher):
    """Same index; the candidate set of a "must also match" value is computed once per distinct value."""

    def _cached_positions(self, condition: dict) -> set:
        cache = self.__dict__.setdefault("_condition_cache", {})
        value = condition.get("source_value")
        key = (condition.get("comparison_method"), condition.get("destination_column"),
               condition.get("numeric_tolerance"), type(value).__name__, value)
        try:
            hit = cache.get(key)
        except TypeError:
            return self._candidate_positions_for_condition(condition)[0]
        if hit is None:
            hit = self._candidate_positions_for_condition(condition)[0]
            cache[key] = hit
        return hit

    def bounded_secondary_candidates(self, source_row: dict, conditions, used_indices, limit: int, target_value):
        """What find_secondary_candidates followed by the token cut returns, without listing every row."""
        sets = []
        for configured in conditions:
            condition = dict(configured)
            condition["source_value"] = source_row.get(str(configured.get("source_column", "")))
            matches = self._cached_positions(condition)
            if not matches:
                return []
            sets.append(matches)
        if not sets:
            return []
        sets.sort(key=len)
        smallest, rest = sets[0], sets[1:]
        indices, rows = self.indices, self.rows
        found = []
        for position in smallest:
            if indices[position] in used_indices:
                continue
            if all(position in other for other in rest):
                found.append(position)
                if len(found) > limit:
                    break
        if len(found) <= limit:
            return [(indices[position], rows[position]) for position in sorted(found)]
        # More than the limit: keep only rows sharing a primary-key token, as before.
        if self.is_composite or is_blank(target_value):
            return []
        token_positions = set(self.compact_map.get(_qw_compact(target_value), []))
        for token in _qw_tokens(target_value, use_synonyms=True):
            token_positions.update(self.token_blocks.get(token, []))
        keep = [p for p in token_positions if indices[p] not in used_indices and all(p in s for s in sets)]
        return [(indices[position], rows[position]) for position in sorted(keep)]


'''

FIX_3_OLD = """    if matcher_type is None:
        matcher_type = detect_matcher_type(
            [file_1_value, file_2_value],
            fields_label(file_1_fields),
            fields_label(file_2_fields),
        )
"""
# Only values identical as read (same type, equal). Comparing normalized text instead would be
# wrong: "-500" and "500" normalize to the same text.
FIX_3_NEW = """    try:
        _identical = type(file_1_value) is type(file_2_value) and bool(file_1_value == file_2_value)
    except Exception:
        _identical = False
    if _identical:
        return {}

""" + FIX_3_OLD

FIX_2_OLD = """            passing, _ = indexed_matcher.find_secondary_candidates(
                file_1_row,
                normalized_secondary_conditions,
                matched_file_2_indices,
            )
            passing_indices = {index for index, _ in passing}
            rejected = exact_candidates
            exact_candidates = [candidate for candidate in exact_candidates if candidate[0] in passing_indices]
"""
FIX_2_NEW = """            rejected = exact_candidates
            exact_candidates = [
                candidate for candidate in exact_candidates
                if _passes_like_index(file_1_row, candidate[1], normalized_secondary_conditions)
            ]
"""

FIX_4_OLD_A = "    indexed_matcher = IndexedCandidateMatcher(file_2_df, file_2_match_keys, key_matcher_types) if has_keys else None\n"
FIX_4_NEW_A = "    indexed_matcher = _FastMatcher(file_2_df, file_2_match_keys, key_matcher_types) if has_keys else None\n"
FIX_4_OLD_B = """            secondary_candidates, _ = indexed_matcher.find_secondary_candidates(
                file_1_row,
                normalized_secondary_conditions,
                matched_file_2_indices,
            )
"""
FIX_4_NEW_B = """            secondary_candidates = indexed_matcher.bounded_secondary_candidates(
                file_1_row, normalized_secondary_conditions, matched_file_2_indices,
                _MAX_DIRECT_SIMILARITY_CANDIDATES, file_1_id,
            )
"""
FIX_4_OLD_C = """            if len(secondary_candidates) > _MAX_DIRECT_SIMILARITY_CANDIDATES:
                similar_positions = indexed_matcher.token_candidate_indices(file_1_id)
                secondary_candidates = [candidate for candidate in secondary_candidates if candidate[0] in similar_positions]
"""

ANCHOR = "REQUIRED_GST_COLUMNS = [\n"


def _swap(source: str, old: str, new: str) -> str:
    assert source.count(old) == 1, f"expected exactly one occurrence of: {old[:60]!r}"
    return source.replace(old, new)


def patched_source(engine_path: Path, fixes: set[str]) -> str:
    source = engine_path.read_text(encoding="utf8")
    fixes = set(fixes) - {"0"}
    if not fixes:
        return source
    source = _swap(source, ANCHOR, PRELUDE + ANCHOR)
    if "3" in fixes:
        source = _swap(source, FIX_3_OLD, FIX_3_NEW)
    if "2" in fixes:
        source = _swap(source, FIX_2_OLD, FIX_2_NEW)
    if "4" in fixes:
        source = _swap(source, FIX_4_OLD_A, FIX_4_NEW_A)
        source = _swap(source, FIX_4_OLD_B, FIX_4_NEW_B)
        source = _swap(source, FIX_4_OLD_C, "")
    return source


def tag(fixes: set[str]) -> str:
    return "".join(sorted(set(fixes) - {"0"})) or "0"
