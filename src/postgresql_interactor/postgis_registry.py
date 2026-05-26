"""
PostGIS function whitelists and associated validators.

Functions are organized by context:
  - _POSTGIS_FUNCTIONS         : SELECT / ORDER BY (computed fields)
  - _POSTGIS_SPATIAL_PREDICATES: WHERE (spatial predicates)
  - _POSTGIS_CONSTRUCTORS      : INSERT / UPDATE (geometry construction)
"""

import re
from typing import Any, Dict, List, Optional, Set

from .exceptions import PostGISError

# ---------------------------------------------------------------------------
# Registries
# ---------------------------------------------------------------------------

_POSTGIS_FUNCTIONS: Dict[str, Optional[int]] = {
    "ST_AsGeoJSON":     1,
    "ST_AsText":        1,
    "ST_AsEWKT":        1,
    "ST_AsBinary":      1,
    "ST_AsEWKB":        1,
    "ST_Area":          1,
    "ST_Length":        1,
    "ST_Perimeter":     1,
    "ST_Distance":      2,
    "ST_3DDistance":    2,
    "ST_MaxDistance":   2,
    "ST_IsValid":       1,
    "ST_IsEmpty":       1,
    "ST_IsRing":        1,
    "ST_IsClosed":      1,
    "ST_Transform":     2,
    "ST_SetSRID":       2,
    "ST_Buffer":        2,
    "ST_Centroid":      1,
    "ST_Envelope":      1,
    "ST_ConvexHull":    1,
    "ST_Simplify":      2,
    "ST_Snap":          3,
    "ST_Union":         None,
    "ST_Intersection":  2,
    "ST_Difference":    2,
    "ST_SymDifference": 2,
    "ST_MakePoint":     None,
    "ST_MakeLine":      None,
    "ST_MakePolygon":   None,
    "ST_Collect":       None,
}

_POSTGIS_SPATIAL_PREDICATES: Dict[str, int] = {
    "ST_Equals":           2,
    "ST_Disjoint":         2,
    "ST_Touches":          2,
    "ST_Within":           2,
    "ST_Overlaps":         2,
    "ST_Contains":         2,
    "ST_Covers":           2,
    "ST_CoveredBy":        2,
    "ST_Crosses":          2,
    "ST_Intersects":       2,
    "ST_DWithin":          3,
    "ST_3DDWithin":        3,
    "ST_ContainsProperly": 2,
}

_POSTGIS_CONSTRUCTORS: Dict[str, Optional[int]] = {
    "ST_GeomFromText":    2,
    "ST_GeomFromEWKT":    1,
    "ST_GeomFromGeoJSON": 1,
    "ST_MakePoint":       None,
    "ST_MakeLine":        None,
    "ST_MakePolygon":     None,
    "ST_SetSRID":         2,
    "ST_Transform":       2,
    "ST_MakeEnvelope":    None,
}

_ALL_POSTGIS_FUNCTIONS: Dict[str, Optional[int]] = {
    **_POSTGIS_FUNCTIONS,
    **_POSTGIS_SPATIAL_PREDICATES,
    **_POSTGIS_CONSTRUCTORS,
}

_ALIAS_RE = re.compile(r"^[A-Za-z0-9_]+$")


# ---------------------------------------------------------------------------
# Validators  (pure functions, no instance dependency)
# ---------------------------------------------------------------------------

def validate_alias_name(alias: str) -> None:
    if not _ALIAS_RE.match(alias):
        raise PostGISError(f"Invalid alias: {alias}")


def validate_postgis_function(
    function: str,
    args: List[Any],
    allowed_registry: Dict[str, Optional[int]],
) -> None:
    if function not in allowed_registry:
        raise PostGISError(f"PostGIS function not allowed: {function}")
    expected = allowed_registry[function]
    if expected is not None and len(args) != expected:
        raise PostGISError(
            f"{function} requires exactly {expected} argument(s), "
            f"got {len(args)}"
        )


def validate_postgis_arg(
    arg: Any,
    allowed_fields: Set[str],
) -> None:
    if isinstance(arg, (int, float)):
        return

    if isinstance(arg, str):
        nested = re.match(r"^(ST_\w+)\s*\(.*\)$", arg, re.IGNORECASE)
        if nested:
            func_name = nested.group(1)
            if func_name not in _ALL_POSTGIS_FUNCTIONS:
                raise PostGISError(
                    f"Nested PostGIS function not allowed: {func_name}"
                )
            return
        if arg.strip() == "%s":
            return
        parts = arg.split(".")
        if len(parts) > 1:
            validate_alias_name(parts[0])
        if parts[-1] not in allowed_fields:
            raise PostGISError(f"Unrecognized PostGIS argument: {arg!r}")
        return

    raise TypeError(
        f"Unsupported PostGIS argument type: {type(arg).__name__}"
    )
