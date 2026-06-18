"""Process simulator for a conventional gold mill flowsheet.

``flowsheet`` builds the clean, physically-correlated time series; ``faults``
injects the deliberate data-quality defects the DQ engine must later catch;
``generate`` ties them together and writes to the raw historian.
"""
