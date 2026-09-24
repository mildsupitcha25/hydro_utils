"""
HYDROMETRICS — Water & Color IoT Telemetry Dashboard (Streamlit)
รัน:  streamlit run app.py
ค่าเริ่มต้น (ลิงก์ชีต / เกณฑ์เตือน / สี) แก้ได้ใน hydro_utils.py
"""
from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from hydro_utils import (
    CSS, DEFAULT_SHEET_URL, METRIC_KEYS, METRICS,
    evaluate, header_html, load_sheet, metric_card_html, tank_html, thermo_html,
)

TZ = ZoneInfo("Asia/Bangkok")
STALE_AFTER_SEC = 120          # ไม่มีข้อมูลใหม่เกินกี่วินาที → แสดงสถานะ "ไม่มีข้อมูลใหม่"
LOCAL_CSV = os.getenv("HYDRO_CSV")  # ใช้ทดสอบกับไฟล์ CSV ในเครื่อง (ไม่บังคับ)
REFRESH_OPTIONS = {"3s": 3, "5s": 5, "10s": 10, "30s": 30, "หยุด": None}

st.set_page_config(page_title="HYDROMETRICS · IoT Telemetry", page_icon="💧", layout="wide")
st.markdown(CSS, unsafe_allow_html=True)


# ------------------------------------------------------------------
# State เริ่มต้น
# ------------------------------------------------------------------
def init_state():
    ss = st.session_state
    ss.setdefault("sheet_url", default_sheet_url())
    ss.setdefault("alert_log", [])
    ss.setdefault("last_alert_ts", None)
    ss.setdefault("toast_on", True)
    for k, m in METRICS.items():
        ss.setdefault(f"th_{k}_on", True)
        ss.setdefault(f"th_{k}_min", m.default_min)
        ss.setdefault(f"th_{k}_max", m.default_max)


def default_sheet_url() -> str:
    """ใช้ SHEET_URL จาก Secrets ของ Streamlit Cloud ถ้ามี ไม่งั้นใช้ค่าใน hydro_utils.py"""
    try:
        return st.secrets["SHEET_URL"]
    except Exception:
        return DEFAULT_SHEET_URL


def thresholds(k: str) -> tuple[float, float, bool]:
    ss = st.session_state
    return ss[f"th_{k}_min"], ss[f"th_{k}_max"], ss[f"th_{k}_on"]


@st.cache_data(ttl=2, show_spinner=False)
def fetch(sheet_url: str) -> tuple[pd.DataFrame, datetime]:
    return load_sheet(sheet_url, LOCAL_CSV), datetime.now(TZ)


def reset_thresholds():
    for k, m in METRICS.items():
        st.session_state[f"th_{k}_min"] = m.default_min
        st.session_state[f"th_{k}_max"] = m.default_max
        st.session_state[f"th_{k}_on"] = True


init_state()


# ------------------------------------------------------------------
# Sidebar: ตั้งค่า
# ------------------------------------------------------------------
with st.sidebar:
    st.markdown("### ⚙️ ตั้งค่า")
    st.text_input("ลิงก์ Google Sheet", key="sheet_url",
                  help="ชีตต้องตั้งแชร์เป็น 'ทุกคนที่มีลิงก์ → ผู้มีสิทธิ์อ่าน'")
    refresh_label = st.radio("ความถี่รีเฟรช", list(REFRESH_OPTIONS), index=1, horizontal=True)
    if st.button("🔄 ดึงข้อมูลเดี๋ยวนี้"):
        fetch.clear()
    st.divider()
    st.markdown("### 🎚️ เกณฑ์เตือน")
    st.caption("ค่าที่ตั้งตรงนี้ใช้เฉพาะหน้าจอนี้ — ถ้าจะเปลี่ยนค่าเริ่มต้นถาวร ให้แก้ใน hydro_utils.py")
    for k, m in METRICS.items():
        with st.expander(f"{m.name_th} ({m.unit})"):
            st.toggle("เปิดการแจ้งเตือน", key=f"th_{k}_on")
            c1, c2 = st.columns(2)
            c1.number_input("ต่ำสุด", key=f"th_{k}_min", step=0.1, format="%.1f")
            c2.number_input("สูงสุด", key=f"th_{k}_max", step=0.1, format="%.1f")
    st.button("คืนค่าเริ่มต้นทั้งหมด", on_click=reset_thresholds)
    st.toggle("แสดงป๊อปอัปเมื่อเกินเกณฑ์", key="toast_on")


# ------------------------------------------------------------------
# Charts
# ------------------------------------------------------------------
def base_layout(fig: go.Figure, height=340, y_title=""):
    fig.update_layout(
        height=height, margin=dict(l=10, r=10, t=10, b=10),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#94a3b8", family="JetBrains Mono, monospace", size=11),
        hovermode="x unified",
        legend=dict(orientation="h", y=-0.18, x=0.5, xanchor="center"),
        xaxis=dict(gridcolor="#1e293b", showline=True, linecolor="#334155"),
        yaxis=dict(gridcolor="#1e293b", title=y_title, zeroline=False),
    )
    return fig


def metric_chart(df: pd.DataFrame, k: str, n: int, kind: str, overlay_key: str | None = None) -> go.Figure:
    m = METRICS[k]
    d = df.head(n).iloc[::-1]
    lo, hi, on = thresholds(k)
    fig = go.Figure()
    if kind == "BAR":
        fig.add_bar(x=d["timestamp"], y=d[k], name=m.name_th, marker_color=m.color)
    else:
        fig.add_scatter(
            x=d["timestamp"], y=d[k], name=m.name_th, mode="lines+markers",
            line=dict(color=m.color, width=2.5), marker=dict(size=5),
            fill="tozeroy" if kind == "AREA" else None,
            fillcolor=_rgba(m.color, 0.18),
        )
    if overlay_key:
        o = METRICS[overlay_key]
        fig.add_scatter(x=d["timestamp"], y=d[overlay_key], name=o.name_th, mode="lines",
                        line=dict(color=o.color, width=1.5, dash="dot"))
    if on:
        fig.add_hline(y=hi, line=dict(color="#f43f5e", dash="dash", width=1.5),
                      annotation_text=f"Max: {hi:g} {m.unit}", annotation_font_color="#f43f5e")
        fig.add_hline(y=lo, line=dict(color="#f59e0b", dash="dash", width=1.5),
                      annotation_text=f"Min: {lo:g} {m.unit}", annotation_font_color="#f59e0b",
                      annotation_position="bottom right")
    vals = pd.concat([d[k]] + ([d[overlay_key]] if overlay_key else [])).dropna()
    if len(vals):
        ymin = min(vals.min(), lo if on else vals.min())
        ymax = max(vals.max(), hi if on else vals.max())
        pad = max((ymax - ymin) * 0.12, 1)
        fig.update_yaxes(range=[ymin - pad, ymax + pad])
    return base_layout(fig, y_title=m.unit)


def _rgba(hex_color: str, a: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{a})"


# ------------------------------------------------------------------
# Alerts
# ------------------------------------------------------------------
def check_alerts(df: pd.DataFrame):
    latest = df.iloc[0]
    ts = latest["timestamp"]
    if st.session_state.last_alert_ts == ts:
        return
    st.session_state.last_alert_ts = ts
    for k, m in METRICS.items():
        lo, hi, on = thresholds(k)
        if not on:
            continue
        s = evaluate(latest[k], lo, hi)
        if s in ("low", "high"):
            cond = f"< {lo:g}" if s == "low" else f"> {hi:g}"
            st.session_state.alert_log.insert(0, {
                "เวลา": ts, "พารามิเตอร์": m.name_th, "ค่า": round(float(latest[k]), 2),
                "หน่วย": m.unit, "เงื่อนไข": cond, "ข้อความ": m.alert_msg,
            })
            if st.session_state.toast_on:
                st.toast(f"⚠️ {m.alert_msg} ({latest[k]:.2f} {m.unit})")
    del st.session_state.alert_log[200:]


# ------------------------------------------------------------------
# หน้า Dashboard (fragment → รีเฟรชเฉพาะส่วนนี้ตามรอบที่เลือก)
# ------------------------------------------------------------------
def dashboard():
    try:
        df, fetched_at = fetch(st.session_state.sheet_url)
    except Exception as e:
        st.error(f"ดึงข้อมูลจาก Google Sheet ไม่สำเร็จ: {e}\n\n"
                 "ตรวจสอบว่าแชร์ชีตเป็น 'ทุกคนที่มีลิงก์' และลิงก์ถูกต้อง")
        return
    if df.empty:
        st.warning("ยังไม่มีข้อมูลในชีต")
        return

    latest = df.iloc[0]
    age = (datetime.now(TZ).replace(tzinfo=None) - latest["timestamp"]).total_seconds()
    st.markdown(header_html(len(df), latest["timestamp"], refresh_label, fetched_at,
                            age < STALE_AFTER_SEC, st.session_state.sheet_url), unsafe_allow_html=True)
    check_alerts(df)

    stats = {k: {"min": df[k].min(), "avg": df[k].mean(), "max": df[k].max()} for k in METRIC_KEYS}

    tabs = st.tabs(["📈 ภาพรวมเรียลไทม์", "💧 ภาพจำลองถัง & เกจ", "📊 กราฟรายตัว",
                    "🧮 กราฟเปรียบเทียบหลายค่า", "🗂️ ตารางประวัติข้อมูล", "🔔 บันทึกการแจ้งเตือน"])

    # ---- Tab 1: Overview ----
    with tabs[0]:
        st.markdown(f'<div class="hm-section"><span>ค่าเซนเซอร์หลัก 5 ตัวแปร (Latest Telemetry)</span>'
                    f'<small>{latest["timestamp"]:%Y-%m-%d %H:%M:%S} · {latest["remark"]}</small></div>',
                    unsafe_allow_html=True)
        cols = st.columns(5)
        for c, k in zip(cols, METRIC_KEYS):
            lo, hi, _ = thresholds(k)
            c.markdown(metric_card_html(METRICS[k], df[k], lo, hi, stats[k]), unsafe_allow_html=True)
        st.write("")
        left, right = st.columns([1.15, 1])
        with left:
            render_tanks(latest, compact=True)
        with right:
            st.markdown("**ระดับความสูงของน้ำ** — 50 จุดล่าสุด")
            st.plotly_chart(metric_chart(df, "waterLevel", 50, "AREA"), key="ov_chart")

    # ---- Tab 2: Tanks ----
    with tabs[1]:
        render_tanks(latest)

    # ---- Tab 3: Per-metric charts ----
    with tabs[2]:
        c1, c2 = st.columns([1, 1])
        n = c1.radio("จำนวนจุด", [20, 50, 100, 500], index=1, horizontal=True, key="pm_n")
        kind = c2.radio("รูปแบบกราฟ", ["AREA", "LINE", "BAR"], horizontal=True, key="pm_kind")
        overlay_pairs = {"waterLevel": "waterSensorDistance", "colorLevel": "colorSensorDistance"}
        grid = st.columns(2)
        for i, k in enumerate(METRIC_KEYS):
            m = METRICS[k]
            with grid[i % 2]:
                with st.container(border=True):
                    lo, hi, on = thresholds(k)
                    st.markdown(f"**{m.name_th}** &nbsp; `{_val(latest[k])} {m.unit}` "
                                f"<span style='color:#64748b;font-size:.8rem'>· เกณฑ์ {lo:g}–{hi:g} "
                                f"{'(ปิดเตือน)' if not on else ''}</span>", unsafe_allow_html=True)
                    ov = None
                    if k in overlay_pairs:
                        if st.checkbox(f"+ {METRICS[overlay_pairs[k]].name_th}", key=f"ov_{k}"):
                            ov = overlay_pairs[k]
                    st.plotly_chart(metric_chart(df, k, n, kind, ov), key=f"pm_{k}")
        st.caption("ปรับเกณฑ์เตือนได้ที่แถบด้านซ้าย (🎚️ เกณฑ์เตือน)")

    # ---- Tab 4: Multi-metric ----
    with tabs[3]:
        c1, c2, c3 = st.columns([2, 1, 1])
        sel = c1.multiselect("เลือกตัวแปร", METRIC_KEYS, default=["waterLevel", "colorLevel", "waterTemperature"],
                             format_func=lambda k: METRICS[k].name_th, key="mm_sel")
        n = c2.selectbox("จำนวนจุด", [50, 100, 200, 500, 1000], index=1, key="mm_n")
        norm = c3.toggle("ปรับสเกล 0–100%", help="เทียบแนวโน้มของค่าที่หน่วยต่างกัน", key="mm_norm")
        d = df.head(n).iloc[::-1]
        fig = go.Figure()
        for k in sel:
            y = d[k]
            if norm:
                rng = (y.max() - y.min()) or 1
                y = (y - y.min()) / rng * 100
            fig.add_scatter(x=d["timestamp"], y=y, name=f"{METRICS[k].name_th} ({METRICS[k].unit})",
                            mode="lines", line=dict(color=METRICS[k].color, width=2))
        st.plotly_chart(base_layout(fig, 460, "%" if norm else "ค่า"), key="mm_chart")

    # ---- Tab 5: Table ----
    with tabs[4]:
        c1, c2, c3 = st.columns([1.3, 1, 1])
        dmin, dmax = df["timestamp"].min().date(), df["timestamp"].max().date()
        rng = c1.date_input("ช่วงวันที่", (dmax, dmax), min_value=dmin, max_value=dmax, key="tb_rng")
        only_alert = c2.toggle("เฉพาะแถวที่เกินเกณฑ์", key="tb_alert")
        rows = c3.selectbox("แสดงสูงสุด", [100, 500, 1000, 5000], index=1, key="tb_rows")
        t = df
        if isinstance(rng, (list, tuple)) and len(rng) == 2:
            t = t[(t["timestamp"].dt.date >= rng[0]) & (t["timestamp"].dt.date <= rng[1])]
        if only_alert:
            mask = pd.Series(False, index=t.index)
            for k in METRIC_KEYS:
                lo, hi, on = thresholds(k)
                if on:
                    mask |= (t[k] < lo) | (t[k] > hi)
            t = t[mask]
        show = t.head(rows).rename(columns={"timestamp": "เวลา", "remark": "หมายเหตุ",
                                            **{k: f"{METRICS[k].name_th} ({METRICS[k].unit})" for k in METRIC_KEYS}})
        st.caption(f"พบ {len(t):,} แถว · แสดง {len(show):,} แถวล่าสุด")
        st.dataframe(show, hide_index=True, height=480,
                     column_config={"เวลา": st.column_config.DatetimeColumn(format="YYYY-MM-DD HH:mm:ss")})
        st.download_button("⬇️ ดาวน์โหลด CSV (ตามตัวกรอง)", t.to_csv(index=False).encode("utf-8-sig"),
                           file_name=f"telemetry_{datetime.now(TZ):%Y%m%d_%H%M}.csv", mime="text/csv")

    # ---- Tab 6: Alert log ----
    with tabs[5]:
        log = st.session_state.alert_log
        if not log:
            st.info("ยังไม่มีการแจ้งเตือนตั้งแต่เปิดหน้านี้")
        else:
            st.dataframe(pd.DataFrame(log), hide_index=True)
            if st.button("ล้างบันทึก"):
                st.session_state.alert_log = []


def render_tanks(latest: pd.Series, compact: bool = False):
    st.markdown(f'<div class="hm-section"><span>ภาพจำลองการทำงานของเซนเซอร์กายภาพ</span>'
                f'<small>สถานะฮาร์ดแวร์: {latest["remark"]}</small></div>', unsafe_allow_html=True)
    c = st.columns(3)
    c[0].markdown(tank_html("ถังน้ำหลัก (Water Tank)", "💧", "US-SENSOR", latest["waterLevel"],
                            latest["waterSensorDistance"], "#22d3ee", "#1d4ed8", "#22d3ee", "ระดับน้ำ"),
                  unsafe_allow_html=True)
    c[1].markdown(tank_html("ถังสารละลายสี (Color Vessel)", "🎨", "OPT-COLOR", latest["colorLevel"],
                            latest["colorSensorDistance"], "#f472b6", "#9d174d", "#f472b6", "ระดับสี"),
                  unsafe_allow_html=True)
    lo, hi, _ = thresholds("waterTemperature")
    c[2].markdown(thermo_html(latest["waterTemperature"], lo, hi), unsafe_allow_html=True)


def _val(v) -> str:
    return "—" if pd.isna(v) else f"{v:.2f}"


# รัน dashboard เป็น fragment ที่รีเฟรชอัตโนมัติ
st.fragment(run_every=REFRESH_OPTIONS[refresh_label])(dashboard)()
