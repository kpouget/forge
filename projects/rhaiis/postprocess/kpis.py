from __future__ import annotations

from projects.caliper.engine.kpi import KpiCatalogEntry, KpiComputationStatus, KpiRecord
from projects.caliper.engine.model import UnifiedRunModel
from projects.guidellm.postprocess.guidellm.dashboard import (
    compute_dashboard_kpis,
    dashboard_kpi_catalog,
)


class RhaiisKpiHandler:
    @staticmethod
    def get_catalog() -> list[KpiCatalogEntry]:
        """Get KPI catalog entries for RHAIIS dashboards."""
        return dashboard_kpi_catalog(prefix="rhaiis")

    @staticmethod
    def compute_kpis(model: UnifiedRunModel) -> tuple[list[KpiRecord], KpiComputationStatus]:
        """Compute dashboard KPIs from unified run model."""
        kpi_records = compute_dashboard_kpis(model, prefix="rhaiis")
        status = KpiComputationStatus.success_status(len(kpi_records))
        return kpi_records, status
