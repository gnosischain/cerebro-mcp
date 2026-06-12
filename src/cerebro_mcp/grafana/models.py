"""LLM-facing Pydantic schema for declaring Grafana dashboards.

The LLM never emits raw Grafana JSON. It builds a `GrafanaDashboardDef`
declaring *intent* — panel role, optional viz, required data_shape, and SQL —
and the compiler (see `compiler.py`) turns that into styled, layout-consistent
Grafana panel JSON.

Validation happens at parse time: the role x viz x shape compatibility matrix
is enforced in the panel model_validator, so an invalid combination fails
before the compiler ever runs.
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from cerebro_mcp.grafana.styles import (
    ALLOWED_UNITS,
    DataShape,
    PanelRole,
    ROLE_ALLOWED_VIZ,
    ROLE_DEFAULT_VIZ,
    VIZ_ACCEPTS_SHAPE,
    Viz,
    bounds_for_unit,
)

_UID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,40}$")
_VAR_NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{0,30}$")


class ThresholdStep(BaseModel):
    """One threshold band. value=None is the base (-Infinity) step."""

    value: float | None = None
    color: Literal["green", "yellow", "orange", "red", "blue", "purple"]


class ValueMapping(BaseModel):
    """state -> display color/text mapping for state_timeline/status_history."""

    state: str = Field(..., min_length=1)
    color: str = Field(..., min_length=1)  # hex or Grafana color name
    text: str = ""


class GrafanaPanelDef(BaseModel):
    title: str = Field(..., min_length=1, max_length=120)
    role: PanelRole
    data_shape: DataShape
    sql_query: str = Field(..., min_length=1)
    viz: Viz | None = None  # defaults via ROLE_DEFAULT_VIZ[role] in compiler
    description: str = ""
    unit: str = "short"
    decimals: int | None = Field(default=None, ge=0, le=6)
    thresholds: list[ThresholdStep] | None = None
    transformations: list[dict] = Field(default_factory=list)  # passthrough
    # table columns that must render as verbatim text (addresses, hashes, ids).
    # Without this Grafana applies the panel's numeric unit to every column and
    # mangles hex strings into numbers.
    text_columns: list[str] = Field(default_factory=list)
    # Optional layout overrides; defaults come from the panel catalog.
    width: Literal[6, 8, 12, 24] | None = None
    height: int | None = Field(default=None, ge=3, le=24)
    # gauge bounds (required for gauge unless the unit implies bounds).
    min: float | None = None
    max: float | None = None
    # Only honored when viz=stat and data_shape=single_value.
    sparkline_sql: str | None = None
    # Required for state_timeline / status_history.
    value_mappings: list[ValueMapping] = Field(default_factory=list)

    @field_validator("unit")
    @classmethod
    def _validate_unit(cls, value: str) -> str:
        if value not in ALLOWED_UNITS:
            raise ValueError(
                f"unit '{value}' not in allowlist. "
                f"Choose one of: {sorted(ALLOWED_UNITS)}"
            )
        return value

    @property
    def effective_viz(self) -> str:
        return self.viz or ROLE_DEFAULT_VIZ[self.role]

    @model_validator(mode="after")
    def _validate_role_viz_shape(self) -> "GrafanaPanelDef":
        viz = self.effective_viz

        # role x viz
        allowed = ROLE_ALLOWED_VIZ[self.role]
        if viz not in allowed:
            raise ValueError(
                f"viz '{viz}' is not allowed for role '{self.role}'. "
                f"Allowed: {sorted(allowed)}"
            )

        # viz x shape
        accepts = VIZ_ACCEPTS_SHAPE[viz]
        if self.data_shape not in accepts:
            raise ValueError(
                f"viz '{viz}' does not accept data_shape "
                f"'{self.data_shape}'. Accepts: {sorted(accepts)}"
            )

        # gauge needs bounds (explicit or unit-implied)
        if viz == "gauge":
            implied = bounds_for_unit(self.unit)
            if implied is None and (self.min is None or self.max is None):
                raise ValueError(
                    "gauge requires explicit min and max unless the unit "
                    "implies bounds (percent -> 0..100, percentunit -> 0..1)"
                )

        # state panels need value mappings (Grafana defaults are unreadable)
        if viz in ("state_timeline", "status_history") and not self.value_mappings:
            raise ValueError(
                f"viz '{viz}' requires value_mappings (state -> color)"
            )

        return self


class GrafanaVariableDef(BaseModel):
    name: str
    type: Literal["interval", "custom"]
    label: str = ""
    # interval: comma-sep durations e.g. "1m,5m,1h,1d"
    # custom: comma-sep values e.g. "ethereum,gnosis,polygon"
    options: str = Field(..., min_length=1)
    default: str = Field(..., min_length=1)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        if not _VAR_NAME_RE.match(value):
            raise ValueError(
                "variable name must match ^[a-zA-Z][a-zA-Z0-9_]{0,30}$"
            )
        return value


class GrafanaDashboardDef(BaseModel):
    uid: str
    title: str = Field(..., min_length=1, max_length=180)
    panels: list[GrafanaPanelDef] = Field(..., min_length=1, max_length=30)
    variables: list[GrafanaVariableDef] = Field(default_factory=list, max_length=8)
    tags: list[str] = Field(default_factory=list, max_length=20)
    refresh: str = "5m"
    time_from: str = "now-30d"
    time_to: str = "now"
    force_overwrite: bool = False  # bypass the cerebro-mcp tag guard
    # When False (default), publish refuses if any panel returns zero rows
    # from the live datasource. Set True for dashboards where an empty panel
    # is legitimate (e.g. an alert table that is normally empty).
    allow_empty: bool = False

    @field_validator("uid")
    @classmethod
    def _validate_uid(cls, value: str) -> str:
        if not _UID_RE.match(value):
            raise ValueError("uid must match ^[a-zA-Z0-9_-]{1,40}$")
        return value

    @model_validator(mode="after")
    def _validate_dashboard(self) -> "GrafanaDashboardDef":
        titles = [p.title.lower().strip() for p in self.panels]
        if len(titles) != len(set(titles)):
            raise ValueError("panel titles must be unique (case-insensitive)")
        var_names = [v.name for v in self.variables]
        if len(var_names) != len(set(var_names)):
            raise ValueError("variable names must be unique")
        return self
