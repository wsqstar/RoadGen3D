"""Shared strip/band semantics for corridor-style street layouts."""

from __future__ import annotations

from typing import Dict, Iterable, List, Sequence, Tuple

DETAILED_SIDE_STRIP_KINDS = (
    "nearroad_furnishing",
    "clear_sidewalk",
    "frontage_reserve",
)
DETAILED_SIDE_STRIP_KIND_SET = frozenset(DETAILED_SIDE_STRIP_KINDS)

_DETAILED_ALLOWED_CATEGORIES: Dict[str, Tuple[str, ...]] = {
    "nearroad_furnishing": ("lamp", "trash", "hydrant", "bollard", "bus_stop", "tree"),
    "clear_sidewalk": ("mailbox", "bench"),
    "frontage_reserve": ("building",),
}


def detailed_strip_band_name(side: str, strip_kind: str) -> str:
    normalized_side = str(side or "").strip().lower()
    normalized_kind = str(strip_kind or "").strip().lower()
    if normalized_side in {"left", "right"} and normalized_kind in DETAILED_SIDE_STRIP_KIND_SET:
        return f"{normalized_side}_{normalized_kind}"
    return normalized_kind or normalized_side


def detailed_strip_band_kind(
    strip_kind: str,
    *,
    side: str = "",
    profile_name: str = "",
) -> str:
    normalized_kind = str(strip_kind or "").strip().lower()
    normalized_side = str(side or "").strip().lower()
    normalized_profile = str(profile_name or "").strip().lower()
    if normalized_kind == "nearroad_furnishing":
        if normalized_side == "right" and normalized_profile == "transit_priority_v1":
            return "transit_edge"
        return "furnishing"
    if normalized_kind == "clear_sidewalk":
        return "clear_path"
    if normalized_kind == "frontage_reserve":
        return "frontage_reserve"
    return normalized_kind


def detailed_strip_allowed_categories(strip_kind: str) -> Tuple[str, ...]:
    return tuple(_DETAILED_ALLOWED_CATEGORIES.get(str(strip_kind or "").strip().lower(), ()))


def detailed_strip_kind_from_band_name(name: str) -> str:
    normalized = str(name or "").strip().lower()
    if normalized in DETAILED_SIDE_STRIP_KIND_SET:
        return normalized
    if normalized.startswith("left_") or normalized.startswith("right_"):
        suffix = normalized.split("_", 1)[1]
        if suffix in DETAILED_SIDE_STRIP_KIND_SET:
            return suffix
    generic_to_kind = {
        "left_furnishing": "nearroad_furnishing",
        "right_furnishing": "nearroad_furnishing",
        "right_transit_edge": "nearroad_furnishing",
        "left_clear_path": "clear_sidewalk",
        "right_clear_path": "clear_sidewalk",
    }
    return str(generic_to_kind.get(normalized, normalized))


def band_name_aliases(
    *,
    band_name: str,
    side: str = "",
    profile_name: str = "",
) -> Tuple[str, ...]:
    normalized_name = str(band_name or "").strip().lower()
    normalized_side = str(side or "").strip().lower()
    normalized_profile = str(profile_name or "").strip().lower()
    aliases: List[str] = []

    def _push(value: str) -> None:
        text = str(value or "").strip().lower()
        if text and text not in aliases:
            aliases.append(text)

    strip_kind = detailed_strip_kind_from_band_name(normalized_name)
    if normalized_name:
        _push(normalized_name)
    if strip_kind in DETAILED_SIDE_STRIP_KIND_SET:
        _push(strip_kind)
        if normalized_side in {"left", "right"}:
            _push(detailed_strip_band_name(normalized_side, strip_kind))
            if strip_kind == "nearroad_furnishing":
                if normalized_side == "left":
                    _push("left_furnishing")
                else:
                    if normalized_profile == "transit_priority_v1":
                        _push("right_transit_edge")
                        _push("right_furnishing")
                    else:
                        _push("right_furnishing")
                        _push("right_transit_edge")
            elif strip_kind == "clear_sidewalk":
                _push("left_clear_path" if normalized_side == "left" else "right_clear_path")
    elif normalized_name in {"left_furnishing", "right_furnishing", "right_transit_edge"}:
        side_name = normalized_side or ("left" if normalized_name == "left_furnishing" else "right")
        _push(detailed_strip_band_name(side_name, "nearroad_furnishing"))
        _push("nearroad_furnishing")
    elif normalized_name in {"left_clear_path", "right_clear_path"}:
        side_name = normalized_side or ("left" if normalized_name == "left_clear_path" else "right")
        _push(detailed_strip_band_name(side_name, "clear_sidewalk"))
        _push("clear_sidewalk")
    return tuple(aliases)


def band_name_matches(
    *,
    candidate_band_name: str,
    target_band_name: str,
    side: str = "",
    profile_name: str = "",
) -> bool:
    candidate_aliases = set(
        band_name_aliases(
            band_name=candidate_band_name,
            side=side,
            profile_name=profile_name,
        )
    )
    target_aliases = set(
        band_name_aliases(
            band_name=target_band_name,
            side=side,
            profile_name=profile_name,
        )
    )
    return bool(candidate_aliases and target_aliases and candidate_aliases.intersection(target_aliases))


def semantic_band_name_for_categories(
    *,
    side: str,
    categories: Sequence[str],
    profile_name: str = "",
) -> str:
    normalized_categories = {str(category or "").strip().lower() for category in categories if str(category or "").strip()}
    normalized_side = str(side or "").strip().lower()
    normalized_profile = str(profile_name or "").strip().lower()
    if "building" in normalized_categories:
        return detailed_strip_band_name(normalized_side, "frontage_reserve")
    if "mailbox" in normalized_categories and normalized_categories <= {"mailbox", "bench"}:
        return detailed_strip_band_name(normalized_side, "clear_sidewalk")
    if "bus_stop" in normalized_categories and normalized_profile == "transit_priority_v1" and normalized_side == "right":
        return "right_transit_edge"
    if normalized_side == "left":
        return "left_furnishing"
    return "right_furnishing"


def has_detailed_strip_profiles(placement_context: object | None) -> bool:
    if placement_context is None:
        return False
    profiles = tuple(getattr(placement_context, "detailed_strip_profiles", ()) or ())
    return bool(profiles)


def iter_detailed_strip_profiles(
    placement_context: object | None,
    *,
    side: str = "",
) -> Tuple[Dict[str, object], ...]:
    if placement_context is None:
        return ()
    profiles = tuple(getattr(placement_context, "detailed_strip_profiles", ()) or ())
    normalized_side = str(side or "").strip().lower()
    if normalized_side not in {"left", "right"}:
        return tuple(dict(profile) for profile in profiles if isinstance(profile, dict))
    return tuple(
        dict(profile)
        for profile in profiles
        if isinstance(profile, dict) and str(profile.get("side", "")).strip().lower() == normalized_side
    )


def coerce_band_rule_kinds(band_name: str, band_kind: str) -> Tuple[str, ...]:
    normalized_name = str(band_name or "").strip().lower()
    normalized_kind = str(band_kind or "").strip().lower()
    rule_kinds: List[str] = []
    if normalized_kind:
        rule_kinds.append(normalized_kind)
    strip_kind = detailed_strip_kind_from_band_name(normalized_name or normalized_kind)
    if strip_kind == "nearroad_furnishing":
        if "furnishing" not in rule_kinds:
            rule_kinds.append("furnishing")
        if normalized_kind == "transit_edge" and "transit_edge" not in rule_kinds:
            rule_kinds.append("transit_edge")
    elif strip_kind == "clear_sidewalk":
        if "clear_path" not in rule_kinds:
            rule_kinds.append("clear_path")
    elif strip_kind == "frontage_reserve":
        if "frontage_reserve" not in rule_kinds:
            rule_kinds.append("frontage_reserve")
    return tuple(rule_kinds)


def resolve_band_by_alias(
    bands: Iterable[object],
    *,
    band_name: str,
    side: str = "",
    profile_name: str = "",
) -> object | None:
    normalized_side = str(side or "").strip().lower()
    aliases = band_name_aliases(
        band_name=band_name,
        side=normalized_side,
        profile_name=profile_name,
    )
    exact_name = str(band_name or "").strip().lower()
    best_band = None
    best_rank: Tuple[int, int, str] | None = None
    for band in bands:
        candidate_name = str(getattr(band, "name", "") or "").strip().lower()
        candidate_side = str(getattr(band, "side", "") or "").strip().lower()
        if normalized_side in {"left", "right"} and candidate_side not in {"", normalized_side}:
            continue
        candidate_aliases = set(
            band_name_aliases(
                band_name=candidate_name,
                side=candidate_side or normalized_side,
                profile_name=profile_name,
            )
        )
        if not candidate_aliases.intersection(aliases):
            continue
        rank = (
            0 if candidate_name == exact_name else 1,
            0 if candidate_side == normalized_side else 1,
            candidate_name,
        )
        if best_rank is None or rank < best_rank:
            best_rank = rank
            best_band = band
    return best_band

