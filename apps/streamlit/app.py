"""Streamlit demo for Social Listening MCP: one chat, tools switched on inside the chat.

Run from the repository root:  streamlit run apps/streamlit/app.py --server.address 127.0.0.1
It needs the MCP server (port 8769), the final review service (port 8771) and an
OpenAI-compatible model endpoint. See apps/streamlit/README.md.
"""

import json
import os
import time

import streamlit as st
from agent import (
    TOOL_GROUPS,
    AgentError,
    download,
    group_tools,
    load_settings,
    openai_chat,
    routed_caller,
    run_turn,
)
from theme import ThemeError, header_html, load_theme, welcome_html

SHORTCUTS = {
    "Tanya data": ("Social Listening",
                   "Berapa jumlah berita tentang banjir dari 1 sampai 14 Oktober 2026, "
                   "dan bagaimana sentimennya?"),
    "Laporan": ("Krakatau reports",
                "Buatkan laporan berita tentang Krakatau dari 1 sampai 14 September 2026."),
    "Status final review": ("Final review",
                            "Bagaimana status final review laporan e4753a09d6b24a94a7682456f5d94c66? "
                            "Versi mana yang disetujui dan apakah sudah dikirim?"),
}

try:
    THEME = load_theme(os.environ.get("SLMCP_THEME_DIR"))
    THEME_ERROR = None
except ThemeError as error:
    THEME, THEME_ERROR = load_theme(None), str(error)
st.set_page_config(page_title=THEME["page_title"], page_icon=THEME["logo_uri"] or "📰", layout="wide",
                   initial_sidebar_state=THEME["sidebar"])
if THEME["css"]:
    st.markdown(f"<style>{THEME['css']}</style>", unsafe_allow_html=True)
if THEME_ERROR:
    st.warning(f"Theme not loaded: {THEME_ERROR}")
settings = load_settings()
URLS = {"data": settings.mcp_url, "review": settings.review_mcp_url}
chat_state = st.session_state.setdefault("chat", {"history": [], "turns": []})
downloads = st.session_state.setdefault("downloads", {})
for label in TOOL_GROUPS:
    st.session_state.setdefault(f"tool:{label}", False)

with st.sidebar:
    st.header("Pengaturan")
    labels = [profile.label for profile in settings.profiles]
    profile = settings.profiles[labels.index(st.selectbox("Model", labels))]
    st.caption(f"Model ID: `{profile.model}` · temperature 0"
               + (" · thinking off" if profile.thinking_off else ""))
    if st.button("Chat baru", use_container_width=True):
        st.session_state["chat"] = {"history": [], "turns": []}
        st.rerun()
    st.divider()
    st.caption(f"Data dan laporan: `{settings.mcp_url}`  \nFinal review: `{settings.review_mcp_url}`")
    st.caption("Angka dihitung oleh tool di server, bukan oleh model. Setiap panggilan tool ditampilkan "
               "dengan argumen dan data mentahnya di samping jawaban.")


@st.cache_data(ttl=60, show_spinner=False)
def specs_for(groups):
    return group_tools(groups, URLS)


def active_groups():
    return tuple(label for label in TOOL_GROUPS if st.session_state[f"tool:{label}"])


def fetch(url):
    if url not in downloads:
        downloads[url] = download(url)
    return downloads[url]


def show_report_actions(turn_index, step):
    result = step.get("result") or {}
    if result.get("approval_url"):
        summary = result.get("summary") or {}
        peak = summary.get("peak") or {}
        if summary:
            st.markdown(f"**{summary.get('total')}** judul · puncak **{peak.get('count')}** "
                        f"pada {peak.get('at')}")
        cols = st.columns(2)
        cols[0].link_button("Buka halaman persetujuan", result["approval_url"], use_container_width=True)
        if cols[1].button("Cek status laporan", key=f"status-{turn_index}", use_container_width=True):
            check_status(result["report_id"])
    if isinstance(result.get("downloads"), dict):
        for column, (ext, url) in zip(st.columns(2), result["downloads"].items(), strict=False):
            try:
                column.download_button(f"Unduh {ext.upper()}", fetch(url), file_name=f"report.{ext}",
                                       key=f"dl-{turn_index}-{ext}", use_container_width=True)
            except (AgentError, OSError) as error:
                column.error(f"{ext.upper()}: {type(error).__name__}")


def show_review_card(step):
    result = step.get("result")
    if not isinstance(result, dict) or "state" not in result:
        return
    receipt = result.get("receipt")
    st.markdown(f"**Status:** `{result.get('state', '?')}` · **Versi:** {result.get('version', '?')} · "
                f"**Pengiriman:** {'simulasi' if receipt else 'belum'}")
    if receipt:
        st.caption(f"Pengiriman simulasi untuk versi {receipt.get('version')} pada {receipt.get('at')}. "
                   "Tidak ada pengiriman nyata.")
    if result.get("review_url"):
        st.link_button("Buka halaman review versi ini", result["review_url"])


def show_tool_panel(turn_index, steps):
    for step in steps:
        with st.container(border=True):
            st.markdown(f"🔧 **{step['name']}**" + ("  ·  ⚠️ error" if step.get("error") else ""))
            st.caption("Argumen dari tombol" if step.get("by_button") else "Argumen dari model")
            st.json(step.get("arguments") or {})
            st.caption("Data mentah dari tool")
            if step.get("result") is not None:
                st.json(step["result"], expanded=1)
            else:
                st.code(step.get("raw") or step.get("error") or "", language="text")
            if step["name"] in ("build_report", "report_status"):
                show_report_actions(turn_index, step)
            if step["name"] == "get_analyst_final_review":
                show_review_card(step)


def show_turn(index, turn):
    with st.chat_message("user"):
        st.markdown(turn["question"])
    with st.chat_message("assistant"):
        if turn["steps"]:
            answer, panel = st.columns([3, 2], gap="medium")
            with panel:
                show_tool_panel(index, turn["steps"])
        else:
            answer = st.container()
        with answer:
            st.markdown(turn["answer"] or "_(tidak ada teks jawaban)_")
            tools = ", ".join(turn["groups"]) if turn["groups"] else "tools mati"
            st.caption(f"{len(turn['steps'])} panggilan tool · {turn['seconds']:.0f} detik · "
                       f"{turn['model']} · {tools}")


def check_status(report_id):
    _, route = specs_for(("Krakatau reports",))
    try:
        text, _ = routed_caller(route)("report_status", {"report_id": report_id})
    except AgentError as error:
        st.error(str(error))
        return
    result = json.loads(text)
    chat_state["turns"].append({
        "question": "Cek status laporan", "answer": f"Status laporan: **{result.get('status')}**",
        "steps": [{"name": "report_status", "arguments": {"report_id": report_id}, "result": result,
                   "raw": text, "error": None, "by_button": True}],
        "seconds": 0, "model": "tanpa model", "groups": ["Krakatau reports"]})
    st.rerun()


def ask(question):
    groups = active_groups()
    started = time.monotonic()
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"), st.spinner("Model sedang bekerja…"):
        try:
            tools, route = specs_for(groups) if groups else ([], {})
            new, steps, answer = run_turn(chat_state["history"], question, openai_chat(profile),
                                          routed_caller(route), tools)
        except AgentError as error:
            st.error(str(error))
            return
    chat_state["history"].extend(new)
    chat_state["turns"].append({"question": question, "steps": steps, "answer": answer,
                                "groups": list(groups), "seconds": time.monotonic() - started,
                                "model": profile.label})
    st.rerun()


st.markdown(header_html(THEME), unsafe_allow_html=True)
bar = st.columns([1.1, 1, 1, 1.3, 2.6])
with bar[0].popover("➕ Tools", use_container_width=True):
    st.caption("Nyalakan tool untuk chat ini")
    for label, (_, names) in TOOL_GROUPS.items():
        st.toggle(label, key=f"tool:{label}", help=", ".join(names))


def shortcut(label):
    """Switch on the shortcut's tool group and queue its question (runs before the next rerun)."""
    group, question = SHORTCUTS[label]
    st.session_state[f"tool:{group}"] = True
    st.session_state["pending"] = question


for column, label in zip(bar[1:4], SHORTCUTS, strict=False):
    column.button(label, use_container_width=True, on_click=shortcut, args=(label,))
pending = st.session_state.pop("pending", None)
on = active_groups()
bar[4].caption("Tools aktif: " + (", ".join(on) if on else "tidak ada (chatbot biasa)"))

if not chat_state["turns"] and not pending:
    st.markdown(welcome_html(THEME), unsafe_allow_html=True)
for index, turn in enumerate(chat_state["turns"]):
    show_turn(index, turn)

typed = st.chat_input("Tanya tentang data berita…")
if pending or typed:
    ask(pending or typed)
