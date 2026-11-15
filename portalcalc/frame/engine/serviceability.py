"""Serviceability limits: deflection divisors and their overrides."""

from portalcalc.frame.common import SERVICEABILITY_LIMIT_DEFAULTS


class ServiceabilityMixin:
    """Deflection limit rules."""

    def serviceability_limit_value(self, key):
        value = self.serviceability_limit_overrides.get(key)
        if value is None:
            value = SERVICEABILITY_LIMIT_DEFAULTS[key]
        return float(value)

    def set_serviceability_limit_value(self, key, value):
        default = SERVICEABILITY_LIMIT_DEFAULTS[key]
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return
        if numeric <= 0.0:
            return
        if abs(numeric - default) < 1e-9:
            self.serviceability_limit_overrides.pop(key, None)
        else:
            self.serviceability_limit_overrides[key] = numeric

    def midspan_deflection_divisor(self):
        key = "truss_combined_il1" if (self.serviceability_importance_level or 1) == 1 else "truss_combined_standard"
        return self.serviceability_limit_value(key)

    def midspan_component_deflection_divisors(self):
        if (self.serviceability_importance_level or 1) == 1:
            return {
                "G": self.serviceability_limit_value("truss_g_il1"),
                "Q": self.serviceability_limit_value("truss_q_il1"),
                "Ws": self.serviceability_limit_value("truss_ws_il1"),
            }
        return {
            "G": self.serviceability_limit_value("truss_g_standard"),
            "Q": self.serviceability_limit_value("truss_q_standard"),
            "Ws": self.serviceability_limit_value("truss_ws_standard"),
        }

    @staticmethod
    def governing_midspan_deflection_components():
        """Components that must be satisfied by member stiffness rather than precamber."""
        return ("Q", "Ws")

    @staticmethod
    def minimum_precamber_required_mm(demand_mm, limit_mm):
        """Minimum upward offset needed to bring permanent-load sag back to its limit."""
        return max(float(demand_mm) - max(float(limit_mm), 0.0), 0.0)

    def midspan_component_deflection_check(self, component, demand_mm, span_mm, divisor, item=None):
        """Classify one component as governing stiffness or permanent-load advisory."""
        limit = span_mm / divisor if span_mm > 0.0 else 0.0
        demand = max(float(demand_mm), 0.0)
        ratio = demand / limit if limit > 1e-12 else float("inf")
        governing = component in self.governing_midspan_deflection_components()
        precamber_required = self.minimum_precamber_required_mm(demand, limit) if component == "G" else 0.0
        if component == "G":
            status = "PRECAMBER REQUIRED" if precamber_required > 1e-9 else "WITHIN LIMIT"
        else:
            status = "OK" if ratio <= 1.0 else "NOT OK"
        return {
            "demand": demand,
            "limit": limit,
            "ratio": ratio,
            "status": status,
            "divisor": divisor,
            "item": item or {},
            "component": component,
            "governing": governing,
            "precamber_required_mm": precamber_required,
        }

    def column_deflection_divisor(self, include_crane_limit=False):
        if include_crane_limit and getattr(self, "serviceability_crane_active", False):
            return self.serviceability_limit_value("crane_rail_height")
        key = "column_il1" if (self.serviceability_importance_level or 1) == 1 else "column_standard"
        return self.serviceability_limit_value(key)

    def eave_deflection_divisor(self):
        override = getattr(self, "eave_deflection_limit_override", None)
        if override is not None and override > 0.0:
            return float(override)
        if getattr(self, "storage_enabled_checkbox", None) is not None and self.storage_enabled_checkbox.isChecked():
            return self.serviceability_limit_value("storage_eave")
        return self.column_deflection_divisor()

    def canopy_tip_deflection_divisor(self):
        key = "canopy_tip_il1" if (self.serviceability_importance_level or 1) == 1 else "canopy_tip_standard"
        return self.serviceability_limit_value(key)
