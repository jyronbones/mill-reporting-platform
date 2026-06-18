"""Governance doc generators must produce clean output."""
from __future__ import annotations

from mill.governance import data_dictionary, kpi_definitions, lineage


def test_data_dictionary_generates(tmp_path):
    out = data_dictionary.generate(tmp_path / "data_dictionary.md")
    text = out.read_text(encoding="utf-8")
    assert "# Data Dictionary" in text
    # every table should be documented
    for table in ["raw_historian", "fact_shift_summary", "dq_results", "dim_tag"]:
        assert f"`{table}`" in text


def test_kpi_definitions_generates(tmp_path):
    out = kpi_definitions.generate(tmp_path / "kpi_definitions.md")
    text = out.read_text(encoding="utf-8")
    assert "# KPI Definitions" in text
    assert "Recovery %" in text
    assert "OEE" in text


def test_lineage_generates(tmp_path):
    out = lineage.generate(tmp_path / "lineage.md")
    text = out.read_text(encoding="utf-8")
    assert "```mermaid" in text
    assert "raw_historian" in text
    assert "Power BI" in text
