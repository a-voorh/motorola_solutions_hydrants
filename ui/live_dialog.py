"""Live Dialog mode: the clean dispatcher workspace (no technical extras)."""

from ui.workspace import render_workspace


def render_live_dialog(hydrants_df):
    """Render the dispatcher-oriented Live Dialog screen."""
    render_workspace(hydrants_df)
