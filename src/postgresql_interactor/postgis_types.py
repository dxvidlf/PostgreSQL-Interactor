from dataclasses import dataclass, field
from typing import Any, List, Optional


@dataclass
class PostGISField:
    """
    PostGIS function call in SELECT or ORDER BY.

    Example::

        PostGISField("ST_AsGeoJSON", ["loc.geom"])
        PostGISField("ST_Distance",  ["a.geom", "b.geom"], alias="dist")
        PostGISField("ST_Transform", ["loc.geom", 4326],   alias="geom_wgs84")
    """

    function: str
    args: List[Any]
    alias: Optional[str] = None
    values: List[Any] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.values is None:
            self.values = []


@dataclass
class PostGISCondition:
    """
    Spatial predicate in WHERE.

    String args that contain ``%s`` are treated as nested expressions;
    the corresponding values must be supplied in ``values`` in order.

    Example::

        PostGISCondition(
            "ST_DWithin",
            ["loc.geom", "ST_MakePoint(%s,%s)::geography", "%s"],
            values=[-5.9, 37.4, 1000],
        )
        PostGISCondition(
            "ST_Within",
            ["geom", "ST_MakeEnvelope(%s,%s,%s,%s,4326)"],
            values=[-6.0, 37.3, -5.8, 37.5],
        )
    """

    function: str
    args: List[Any]
    values: List[Any] = field(default_factory=list)
    negate: bool = False

    def __post_init__(self) -> None:
        if self.values is None:
            self.values = []


@dataclass
class PostGISValue:
    """
    Geometry value for INSERT/UPDATE, built using a PostGIS constructor.

    All arguments are parameterised with ``%s``.

    Example::

        PostGISValue("ST_GeomFromText", ["POINT(-5.9 37.4)", 4326])
        PostGISValue("ST_MakePoint",    [-5.9, 37.4])
    """

    function: str
    args: List[Any]


@dataclass
class PostGISKnnOrder:
    """
    ORDER BY using the KNN distance operator ``<->``.

    Example::

        PostGISKnnOrder(
            "p.geom",
            "ST_Transform(ST_SetSRID(ST_MakePoint(%s,%s), %s), %s)",
            values=[x, y, 25830, 4326],
        )
    """

    left: str
    right: str
    values: List[Any] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.values is None:
            self.values = []
