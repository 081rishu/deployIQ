"""Streamlit test harness for the adaptive interviewer.

Runs the interviewer engine directly (no FastAPI server needed). The
assessment state is kept in st.session_state across turns.
"""

from __future__ import annotations

import streamlit as st

from interviewer.engine import run_turn
from schemas.assessment_state import AssessmentState, Sector

STATUS_EMOJI = {
    "interviewing": "\u2753",  # help
    "clarifying": "\u26a0\ufe0f",  # warning
    "ready": "\u2705",          # check
    "uncertain": "\u26d4",      # no entry
}


def _init_state() -> None:
    if "state" not in st.session_state:
        st.session_state.state = None
    if "messages" not in st.session_state:
        st.session_state.messages = []


def _start(sector: Sector, problem: str) -> None:
    state = AssessmentState(sector=sector, problem=problem)
    st.session_state.state = state
    st.session_state.messages = []
    st.session_state.messages.append(("user", problem))
    _run(state, problem)


def _run(state: AssessmentState, message: str) -> None:
    result = run_turn(state, message)
    st.session_state.state = result.state
    ack = result.acknowledgment or ""
    q = result.question or ""
    st.session_state.messages.append(("assistant", (ack + " " + q).strip()))


def _render_chat() -> None:
    for role, text in st.session_state.messages:
        if text:
            with st.chat_message(role):
                st.write(text)


def main() -> None:
    st.set_page_config(page_title="Interviewer Test", page_icon="\u2699\ufe0f")
    _init_state()

    st.title("AI Deployment Decision Engine \u2014 Interviewer Test")
    st.caption("Drives the adaptive 4-state interviewer. Watch status + need_type.")

    state: AssessmentState | None = st.session_state.state

    if state is None:
        col1, col2 = st.columns(2)
        sector = col1.selectbox(
            "Sector",
            options=[Sector.CUSTOMER_SUPPORT, Sector.DOCUMENT_PROCESSING],
            format_func=lambda s: s.value,
        )
        problem = col2.text_input("Business problem", value="we want to automate support tickets")
        if st.button("Start interview", type="primary"):
            _start(sector, problem)
            st.rerun()
        return

    # Status banner.
    status = state.status.value
    st.write(
        f"{STATUS_EMOJI.get(status, '')} **status:** `{status}`"
        f" &nbsp; **turns:** {state.turn_count}"
    )
    if state.complete:
        st.warning(state.stop_reason or "interview complete")
        if st.button("Reset", type="secondary"):
            st.session_state.state = None
            st.session_state.messages = []
            st.rerun()

    _render_chat()

    if state.complete:
        return

    prompt = st.chat_input("Your answer...")
    if prompt:
        st.session_state.messages.append(("user", prompt))
        _run(state, prompt)
        st.rerun()


if __name__ == "__main__":
    main()
