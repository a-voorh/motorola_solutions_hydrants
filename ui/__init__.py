"""UI layer: Streamlit-only rendering components.

These components call ``workflow``/``domain`` functions and render the results;
they contain no optimisation or parsing logic.
"""

from ui.components import (
    comparison_rows,
    describe_event,
    render_chat_log,
    render_comparison,
    render_flow_status,
    render_result,
    selected_rows,
)

__all__ = [
    "comparison_rows",
    "describe_event",
    "render_chat_log",
    "render_comparison",
    "render_flow_status",
    "render_result",
    "selected_rows",
]
