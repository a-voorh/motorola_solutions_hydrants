"""Water Supply Assistant: top-level entry point with a top navigation bar.

Run:

    pip install streamlit scipy pandas numpy nbformat
    pip install geopy networkx osmnx folium streamlit-folium
    streamlit run app.py

The top nav exposes the App (dispatcher workspace with Live Dialog / Scripts
modes), Visualization, and Model Playground pages. The App page loads the shared
hydrant database and dispatches to the dispatcher-oriented Live Dialog or the
technical Scripts interface, both operating on the same ``st.session_state``.

The dialog (chat history) is cleared whenever the dispatcher switches mode or
navigates to a different page, while the committed incident plan is preserved.
"""

import streamlit as st

from data import get_hydrants
from ui.live_dialog import render_live_dialog
from ui.scripts import render_scripts
from ui.workspace import clear_dialog

st.set_page_config(page_title="Water Supply Assistant", layout="wide")


def app_page():
    st.title("Water Supply Assistant")

    hydrants_df = get_hydrants()

    mode = st.radio(
        "Mode",
        options=["Live Dialog", "Scripts"],
        index=0,
        key="app_mode",
        on_change=clear_dialog,
        horizontal=True,
    )

    if mode == "Live Dialog":
        render_live_dialog(hydrants_df)
    else:
        render_scripts(hydrants_df)


pg = st.navigation(
    [
        st.Page(app_page, title="App", default=True),
        st.Page("visualization_page.py", title="Visualization"),
        st.Page("model_playground_page.py", title="Model Playground"),
    ],
    position="top",
)

# Clear the dialog whenever the dispatcher navigates to a different page.
if st.session_state.get("_current_page") != pg.url_path:
    clear_dialog()
    st.session_state["_current_page"] = pg.url_path

pg.run()
