from typing import Any, List, Optional


class PostGISField:
    """
    Represents a PostGIS function call in SELECT or ORDER BY.

    Example:
        PostGISField("ST_AsGeoJSON", ["loc.geom"])
        PostGISField("ST_Distance",  ["a.geom", "b.geom"], alias="dist")
        PostGISField("ST_Transform", ["loc.geom", 4326],   alias="geom_wgs84")
    """
    def __init__(
        self,
        function: str,
        args: List[Any],
        alias: Optional[str] = None,
        values: Optional[List[Any]] = None,
    ) -> None:
        self.function = function
        self.args     = args     # str = field / nested expression; int/float = literal
        self.alias    = alias
        self.values   = values or []


class PostGISCondition:
    """
    Represents a spatial predicate in WHERE.

    The str-type args containing %s are used as nested expressions;
    actual values are passed in `values` in the same order.

    Example:
        PostGISCondition(
            "ST_DWithin",
            ["loc.geom", "ST_MakePoint(%s,%s)::geography", "%s"],
            values=[-5.9, 37.4, 1000]
        )
        PostGISCondition("ST_Within", ["geom", "ST_MakeEnvelope(%s,%s,%s,%s,4326)"],
                         values=[-6.0, 37.3, -5.8, 37.5])
    """
    def __init__(
        self,
        function: str,
        args: List[Any],
        values: Optional[List[Any]] = None,
        negate: bool = False,
    ) -> None:
        self.function = function
        self.args     = args
        self.values   = values or []
        self.negate   = negate   # True -> NOT ST_xxx(...)


class PostGISValue:
    """
    Represents a geometry value in INSERT/UPDATE constructed using a PostGIS
    function. All arguments are parameterized with %s.

    Example:
        PostGISValue("ST_GeomFromText", ["POINT(-5.9 37.4)", 4326])
        PostGISValue("ST_MakePoint",    [-5.9, 37.4])
    """
    def __init__(self, function: str, args: List[Any]) -> None:
        self.function = function
        self.args     = args


class PostGISKnnOrder:
    """
    Represents an ORDER BY that uses the KNN distance operator <->.

    Example:
        PostGISKnnOrder("p.geom",
                        "ST_Transform(ST_SetSRID(ST_MakePoint(%s,%s), %s), %s)",
                        values=[x, y, 25830, 4326])
    """
    def __init__(
        self,
        left: str,
        right: str,
        values: Optional[List[Any]] = None,
    ) -> None:
        self.left   = left
        self.right  = right
        self.values = values or []
