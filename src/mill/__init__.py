"""Mill Operational Data & Reporting Platform.

A synthetic, end-to-end operational data stack for a conventional gold mill:
process simulator -> PI-style raw historian -> SQL star-schema warehouse ->
KPI semantic layer -> data-quality + mass-balance engine -> governance docs.

The Power BI report is built by hand on top of the KPI layer.
"""

__version__ = "0.1.0"
