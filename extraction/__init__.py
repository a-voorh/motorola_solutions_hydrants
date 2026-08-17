"""Extraction layer: transcript -> facts.

Public API:
  * ``extract_flow``     -- flow (L/min) + whether it was explicitly stated.
  * ``extract_location`` -- incident (latitude, longitude) if present in text.
  * ``detect_update``    -- one radio message -> :class:`UpdateFacts`.

The deterministic regex parser is the first implementation; a future AI
extractor should expose the same functions.
"""

from extraction.parser import detect_update, extract_flow, extract_location

__all__ = ["extract_flow", "extract_location", "detect_update"]
