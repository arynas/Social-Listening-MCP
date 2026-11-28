"""Streamlit demo for Social Listening MCP: ask the data, build a checked report, read a final review.

Run from the repository root:  streamlit run apps/streamlit/app.py --server.address 127.0.0.1
It needs the MCP server (port 8769), the final review service (port 8771) and an
OpenAI-compatible model endpoint. See apps/streamlit/README.md.
"""

import json
import time

import streamlit as st
from agent import (
    DATA_TOOLS,
    REPORT_TOOLS,
    AgentError,
    download,
    load_settings,
    mcp_caller,
    mcp_tools,
    openai_chat,
    report_links,
    review_status,
    run_turn,
)

st.set_page_config(page_title="Social Listening MCP demo", page_icon="📰", layout="wide")
settings = load_settings()

with st.sidebar:
    st.header("Pengaturan")
    labels = [profile.label for profile in settings.profiles]
    profile = settings.profiles[labels.index(st.selectbox("Model", labels))]
    st.caption(f"Model ID: `{profile.model}` · temperature 0"
               + (" · thinking off" if profile.thinking_off else ""))
    st.caption(f"Data tools: `{settings.mcp_url}`")
    st.caption(f"Final review: `{settings.review_mcp_url}`")
    if st.button("Mulai percakapan baru", use_container_width=True):
        for key in ("data", "report"):
            st.session_state.pop(key, None)
        st.rerun()
    st.divider()
    st.caption("Angka dihitung oleh tool di server, bukan oleh model. Setiap panggilan tool "
               "ditampilkan dengan argumen dan hasil mentahnya.")


def state(key):
    return st.session_state.setdefault(key, {"history": [], "turns": []})


@st.cache_data(ttl=60, show_spinner=False)
def tool_specs(url, allowed):
    return mcp_tools(url, allowed)


def show_steps(steps):
    for step in steps:
        label = f"🔧 {step['name']}" + (" — error" if step.get("error") else "")
        with st.expander(label, expanded=True):
            st.markdown("**Argumen dari tombol**" if step.get("by_button") else "**Argumen dari model**")
            st.json(step.get("arguments") or {})
            st.markdown("**Hasil mentah dari tool**")
            if step.get("result") is not None:
                st.json(step["result"], expanded=False)
            else:
                st.code(step.get("raw") or step.get("error") or "", language="text")


def show_turns(turns):
    for turn in turns:
        with st.chat_message("user"):
            st.markdown(turn["question"])
        with st.chat_message("assistant"):
            show_steps(turn["steps"])
            st.markdown(turn["answer"] or "_(tidak ada teks jawaban)_")
            st.caption(f"{len(turn['steps'])} panggilan tool · {turn['seconds']:.0f} detik · {turn['model']}")


def ask(key, question, allowed, url):
    convo = state(key)
    started = time.monotonic()
    try:
        tools = tool_specs(url, allowed)
        new, steps, answer = run_turn(convo["history"], question, openai_chat(profile),
                                      mcp_caller(url), tools)
    except AgentError as error:
        st.error(str(error))
        return
    convo["history"].extend(new)
    convo["turns"].append({"question": question, "steps": steps, "answer": answer,
                           "seconds": time.monotonic() - started, "model": profile.label})


def question_form(key, placeholder):
    with st.form(f"{key}-form", clear_on_submit=True):
        question = st.text_area("Pertanyaan", placeholder=placeholder, height=80, key=f"{key}-q")
        sent = st.form_submit_button("Kirim", type="primary")
    return question.strip() if sent and question.strip() else None


data_tab, report_tab, review_tab = st.tabs(["Tanya data", "Laporan", "Final review"])

with data_tab:
    st.subheader("Tanya data berita")
    st.caption("Tool: " + ", ".join(f"`{name}`" for name in DATA_TOOLS)
               + ". Data publik GDELT 1 Sep – 14 Okt 2026 (WIB).")
    question = question_form("data", "Berapa jumlah berita tentang banjir dari 1 sampai 14 Oktober 2026, "
                                     "dan bagaimana sentimennya?")
    if question:
        with st.spinner("Model sedang bekerja…"):
            ask("data", question, DATA_TOOLS, settings.mcp_url)
    show_turns(reversed(state("data")["turns"]))

with report_tab:
    st.subheader("Laporan berita (4 slide)")
    st.caption("Model menyiapkan outline. Render baru dimulai setelah Anda menyetujui di halaman "
               "persetujuan. Angka di slide dicek oleh kode sebelum tautan unduhan diberikan.")
    question = question_form("report", "Buatkan laporan berita tentang Krakatau dari 1 sampai 14 "
                                       "September 2026.")
    if question:
        with st.spinner("Model sedang bekerja…"):
            ask("report", question, REPORT_TOOLS, settings.mcp_url)

    steps = [step for turn in state("report")["turns"] for step in turn["steps"]]
    links = report_links(steps)
    if links.get("approval_url"):
        box = st.container(border=True)
        box.markdown(f"**Laporan** `{links.get('report_id', '')}` · status: **{links.get('status', '?')}**")
        summary = links.get("summary") or {}
        if summary:
            peak = summary.get("peak") or {}
            box.markdown(f"Total: **{summary.get('total')}** judul · puncak: **{peak.get('count')}** "
                         f"judul pada {peak.get('at')}")
        cols = box.columns(2)
        cols[0].link_button("1. Buka halaman persetujuan", links["approval_url"], use_container_width=True)
        if cols[1].button("2. Cek status laporan", use_container_width=True):
            try:
                text, _ = mcp_caller(settings.mcp_url)("report_status", {"report_id": links["report_id"]})
            except AgentError as error:
                st.error(str(error))
            else:
                result = json.loads(text)
                state("report")["turns"].append({
                    "question": "(tombol) Cek status laporan",
                    "steps": [{"name": "report_status", "arguments": {"report_id": links["report_id"]},
                               "result": result, "raw": text, "error": None, "by_button": True}],
                    "answer": f"Status: **{result.get('status')}**", "seconds": 0, "model": "tanpa model"})
                st.rerun()
        if isinstance(links.get("downloads"), dict):
            for column, (ext, url) in zip(box.columns(2), links["downloads"].items(), strict=False):
                try:
                    data = download(url)
                except (AgentError, OSError) as error:
                    column.error(f"{ext.upper()}: {type(error).__name__}")
                else:
                    column.download_button(f"Unduh {ext.upper()}", data, file_name=f"report.{ext}",
                                           use_container_width=True)
    show_turns(reversed(state("report")["turns"]))

with review_tab:
    st.subheader("Final review (hanya baca)")
    st.caption("Menampilkan status persetujuan akhir dari Open Analyst. Keputusan setuju, minta "
               "perubahan, atau tolak hanya dilakukan manusia di halaman review.")
    with st.form("review-form"):
        review_id = st.text_input("Review ID", placeholder="e4753a09d6b24a94a7682456f5d94c66")
        looked = st.form_submit_button("Lihat status", type="primary")
    if looked and review_id.strip():
        try:
            result, raw = review_status(mcp_caller(settings.review_mcp_url), review_id)
        except AgentError as error:
            st.error(str(error))
        else:
            receipt = result.get("receipt")
            cols = st.columns(3)
            cols[0].metric("Status", result.get("state", "?"))
            cols[1].metric("Versi saat ini", result.get("version", "?"))
            cols[2].metric("Pengiriman", "simulasi tercatat" if receipt else "belum")
            if receipt:
                st.info(f"Pengiriman **simulasi** untuk versi {receipt.get('version')} "
                        f"pada {receipt.get('at')}. Tidak ada pengiriman nyata.")
            if result.get("review_url"):
                st.link_button("Buka halaman review versi ini", result["review_url"])
            st.caption(f"Digest versi: `{result.get('digest')}`")
            with st.expander("🔧 get_analyst_final_review — hasil mentah", expanded=False):
                st.json(result)
