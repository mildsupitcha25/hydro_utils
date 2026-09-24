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
    cards_grid_html, evaluate, filter_window, header_html, load_sheet, metric_card_html,
    remove_outliers, summary_stats, tank_html, thermo_html,
)

TZ = ZoneInfo("Asia/Bangkok")
STALE_AFTER_SEC = 120               # ไม่มีข้อมูลใหม่เกินกี่วินาที → แสดง "ไม่มีข้อมูลใหม่"
MIN_REFRESH_SEC = 2                 # รีเฟรชถี่สุดได้เท่านี้ (กันโหลด Google Sheet หนักเกิน)
LOCAL_CSV = os.getenv("HYDRO_CSV")  # ใช้ทดสอบกับไฟล์ CSV ในเครื่อง (ไม่บังคับ)

STATS_WINDOWS = {"15 นาที": 15, "1 ชั่วโมง": 60, "6 ชั่วโมง": 360, "24 ชั่วโมง": 1440,
                 "7 วัน": 10080, "ทั้งหมด": None}
RESAMPLE_RULES = {"1 นาที": "1min", "5 นาที": "5min", "15 นาที": "15min", "1 ชั่วโมง": "1h", "1 วัน": "1D"}

st.set_page_config(page_title="HYDROMETRICS · IoT Telemetry", page_icon="💧", layout="wide")
st.markdown(CSS, unsafe_allow_html=True)


# ------------------------------------------------------------------
# State เริ่มต้น
# ------------------------------------------------------------------
def default_sheet_url() -> str:
    """ใช้ SHEET_URL จาก Secrets ของ Streamlit Cloud ถ้ามี ไม่งั้นใช้ค่าใน hydro_utils.py"""
    try:
        return st.secrets["SHEET_URL"]
    except Exception:
        return DEFAULT_SHEET_URL


def init_state():
    ss = st.session_state
    ss.setdefault("sheet_url", default_sheet_url())
    ss.setdefault("alert_log", [])
    ss.setdefault("last_alert_ts", None)
    ss.setdefault("toast_on", True)
    ss.setdefault("auto_refresh", True)
    ss.setdefault("refresh_val", 5)
    ss.setdefault("refresh_unit", "วินาที")
    ss.setdefault("stats_window", "1 ชั่วโมง")
    ss.setdefault("clean_outliers", True)
    ss.setdefault("ma_window", 10)
    for k, m in METRICS.items():
        ss.setdefault(f"th_{k}_on", True)
        ss.setdefault(f"th_{k}_min", m.default_min)
        ss.setdefault(f"th_{k}_max", m.default_max)


def thresholds(k: str) -> tuple[float, float, bool]:
    ss = st.session_state
    return ss[f"th_{k}_min"], ss[f"th_{k}_max"], ss[f"th_{k}_on"]


def reset_thresholds():
    for k, m in METRICS.items():
        st.session_state[f"th_{k}_min"] = m.default_min
        st.session_state[f"th_{k}_max"] = m.default_max
        st.session_state[f"th_{k}_on"] = True


@st.cache_data(ttl=MIN_REFRESH_SEC, show_spinner=False)
def fetch(sheet_url: str) -> tuple[pd.DataFrame, datetime]:
    return load_sheet(sheet_url, LOCAL_CSV), datetime.now(TZ)


init_state()


# ------------------------------------------------------------------
# Sidebar: ตั้งค่า
# ------------------------------------------------------------------
with st.sidebar:
    st.markdown("### ⏱️ การรีเฟรช")
    st.toggle("รีเฟรชอัตโนมัติ", key="auto_refresh")
    c1, c2 = st.columns([1, 1])
    c1.number_input("ทุก ๆ", min_value=1, max_value=999, step=1, key="refresh_val")
    c2.selectbox("หน่วย", ["วินาที", "นาที"], key="refresh_unit")
    refresh_sec = st.session_state.refresh_val * (60 if st.session_state.refresh_unit == "นาที" else 1)
    refresh_sec = max(MIN_REFRESH_SEC, refresh_sec)
    refresh_label = (f"{st.session_state.refresh_val} {st.session_state.refresh_unit}"
                     if st.session_state.auto_refresh else "หยุด")
    if st.button("🔄 ดึงข้อมูลเดี๋ยวนี้"):
        fetch.clear()

    st.divider()
    st.markdown("### 📐 สถิติ")
    st.selectbox("ช่วงเวลาที่ใช้คำนวณสถิติ", list(STATS_WINDOWS), key="stats_window",
                 help="ใช้กับ MIN/AVG/MAX บนการ์ด และแท็บสถิติ (นับย้อนจากข้อมูลล่าสุด)")
    st.number_input("ค่าเฉลี่ยเคลื่อนที่ (จำนวนจุด)", min_value=2, max_value=500, step=1, key="ma_window",
                    help="เส้นค่าเฉลี่ยย้อนหลัง N จุดบนกราฟ")
    st.toggle("กรองค่าผิดปกติของเซนเซอร์", key="clean_outliers",
              help="ตัดค่า ≤ 0 หรือเกินช่วงที่เป็นไปได้ (valid_max ใน hydro_utils.py) ออกจากสถิติและกราฟ "
                   "— ค่าปัจจุบันและการแจ้งเตือนยังใช้ค่าดิบ")

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
def _rgba(hex_color: str, a: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{a})"


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


def add_threshold_lines(fig: go.Figure, k: str, vertical: bool = False):
    lo, hi, on = thresholds(k)
    if not on:
        return
    unit = METRICS[k].unit
    add = fig.add_vline if vertical else fig.add_hline
    kw = "x" if vertical else "y"
    add(**{kw: hi}, line=dict(color="#f43f5e", dash="dash", width=1.5),
        annotation_text=f"Max: {hi:g} {unit}", annotation_font_color="#f43f5e")
    add(**{kw: lo}, line=dict(color="#f59e0b", dash="dash", width=1.5),
        annotation_text=f"Min: {lo:g} {unit}", annotation_font_color="#f59e0b",
        annotation_position="bottom right" if not vertical else "top left")


def metric_chart(df: pd.DataFrame, k: str, n: int, kind: str, overlay_key: str | None = None,
                 show_ma: bool = True, show_mean: bool = False, show_band: bool = False) -> go.Figure:
    m = METRICS[k]
    w = int(st.session_state.ma_window)
    lo, hi, on = thresholds(k)
    # ดึงเกินมา w จุด เพื่อให้เส้นค่าเฉลี่ยเคลื่อนที่มีค่าตั้งแต่จุดแรกของกราฟ
    full = df.head(n + w).iloc[::-1]
    ma = full[k].rolling(w, min_periods=max(1, w // 2)).mean().tail(n)
    d = full.tail(n)

    fig = go.Figure()
    if kind == "BAR":
        fig.add_bar(x=d["timestamp"], y=d[k], name=m.name_th, marker_color=m.color)
    else:
        fig.add_scatter(
            x=d["timestamp"], y=d[k], name=m.name_th, mode="lines+markers",
            line=dict(color=m.color, width=2), marker=dict(size=4),
            fill="tozeroy" if kind == "AREA" else None, fillcolor=_rgba(m.color, 0.15),
            connectgaps=False,
        )
    if show_ma:
        fig.add_scatter(x=d["timestamp"], y=ma, name=f"ค่าเฉลี่ยเคลื่อนที่ {w} จุด", mode="lines",
                        line=dict(color="#f8fafc", width=2.2))
    mean, sd = d[k].mean(), d[k].std()
    if show_band and pd.notna(sd):
        fig.add_hrect(y0=mean - sd, y1=mean + sd, fillcolor=_rgba(m.color, 0.10), line_width=0,
                      annotation_text="±1 SD", annotation_position="top left",
                      annotation_font_color="#64748b")
    if show_mean and pd.notna(mean):
        fig.add_hline(y=mean, line=dict(color="#94a3b8", dash="dot", width=1.5),
                      annotation_text=f"เฉลี่ย: {mean:.2f}", annotation_font_color="#cbd5e1",
                      annotation_position="top left")
    if overlay_key:
        o = METRICS[overlay_key]
        fig.add_scatter(x=d["timestamp"], y=d[overlay_key], name=o.name_th, mode="lines",
                        line=dict(color=o.color, width=1.5, dash="dot"))
    add_threshold_lines(fig, k)

    vals = pd.concat([d[k]] + ([d[overlay_key]] if overlay_key else [])).dropna()
    if len(vals):
        ymin = min(vals.min(), lo) if on else vals.min()
        ymax = max(vals.max(), hi) if on else vals.max()
        pad = max((ymax - ymin) * 0.12, 1)
        fig.update_yaxes(range=[ymin - pad, ymax + pad])
    return base_layout(fig, y_title=m.unit)


def resample_chart(dfw: pd.DataFrame, k: str, rule: str) -> go.Figure:
    """ค่าเฉลี่ยรายช่วงเวลา + แถบ min–max"""
    m = METRICS[k]
    g = dfw.set_index("timestamp")[k].sort_index().resample(rule).agg(["mean", "min", "max"]).dropna()
    fig = go.Figure()
    fig.add_scatter(x=g.index, y=g["max"], mode="lines", line=dict(width=0), showlegend=False,
                    hoverinfo="skip")
    fig.add_scatter(x=g.index, y=g["min"], mode="lines", line=dict(width=0), fill="tonexty",
                    fillcolor=_rgba(m.color, 0.18), name="ช่วง min–max")
    fig.add_scatter(x=g.index, y=g["mean"], mode="lines+markers", name="ค่าเฉลี่ย",
                    line=dict(color=m.color, width=2.5), marker=dict(size=5))
    add_threshold_lines(fig, k)
    return base_layout(fig, 380, m.unit)


def histogram_chart(dfw: pd.DataFrame, k: str) -> go.Figure:
    m = METRICS[k]
    s = dfw[k].dropna()
    fig = go.Figure()
    fig.add_histogram(x=s, nbinsx=40, marker_color=m.color, opacity=0.85, name="จำนวน")
    if len(s):
        fig.add_vline(x=s.mean(), line=dict(color="#f8fafc", dash="dot", width=1.5),
                      annotation_text=f"เฉลี่ย {s.mean():.2f}", annotation_font_color="#f8fafc")
    add_threshold_lines(fig, k, vertical=True)
    fig = base_layout(fig, 380, "จำนวนครั้ง")
    fig.update_layout(hovermode="closest", bargap=0.05)
    fig.update_xaxes(title=m.unit)
    return fig


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
# หน้า Dashboard (fragment → รีเฟรชเฉพาะส่วนนี้ตามรอบที่ตั้ง)
# ------------------------------------------------------------------
def dashboard():
    try:
        raw, fetched_at = fetch(st.session_state.sheet_url)
    except Exception as e:
        st.error(f"ดึงข้อมูลจาก Google Sheet ไม่สำเร็จ: {e}\n\n"
                 "ตรวจสอบว่าแชร์ชีตเป็น 'ทุกคนที่มีลิงก์' และลิงก์ถูกต้อง")
        return
    if raw.empty:
        st.warning("ยังไม่มีข้อมูลในชีต")
        return

    # raw = ค่าดิบ (ใช้กับค่าปัจจุบัน/แจ้งเตือน/ตาราง), df = ค่าสำหรับสถิติและกราฟ
    df = remove_outliers(raw) if st.session_state.clean_outliers else raw
    win_label = st.session_state.stats_window
    dfw = filter_window(df, STATS_WINDOWS[win_label])
    latest = raw.iloc[0]
    age = (datetime.now(TZ).replace(tzinfo=None) - latest["timestamp"]).total_seconds()
    st.markdown(header_html(len(raw), latest["timestamp"], refresh_label, fetched_at,
                            age < STALE_AFTER_SEC, st.session_state.sheet_url), unsafe_allow_html=True)
    check_alerts(raw)

    stats = {k: {"min": dfw[k].min(), "avg": dfw[k].mean(), "max": dfw[k].max()} for k in METRIC_KEYS}

    tabs = st.tabs(["📈 ภาพรวมเรียลไทม์", "💧 ภาพจำลองถัง & เกจ", "📊 กราฟรายตัว",
                    "🧮 กราฟเปรียบเทียบหลายค่า", "📐 สถิติ", "🗂️ ตารางประวัติข้อมูล",
                    "🔔 บันทึกการแจ้งเตือน"])

    # ---- Tab 1: Overview ----
    with tabs[0]:
        st.markdown(f'<div class="hm-section"><span>ค่าเซนเซอร์หลัก 5 ตัวแปร (Latest Telemetry)</span>'
                    f'<small>{latest["timestamp"]:%Y-%m-%d %H:%M:%S} · {latest["remark"]}</small></div>',
                    unsafe_allow_html=True)
        cards = []
        for k in METRIC_KEYS:
            lo, hi, _ = thresholds(k)
            cards.append(metric_card_html(METRICS[k], raw[k], lo, hi, stats[k], win_label))
        st.markdown(cards_grid_html(cards), unsafe_allow_html=True)
        st.write("")
        left, right = st.columns([1.15, 1])
        with left:
            render_tanks(latest)
        with right:
            st.markdown(f"**ระดับความสูงของน้ำ** — 50 จุดล่าสุด + ค่าเฉลี่ยเคลื่อนที่ "
                        f"{st.session_state.ma_window} จุด")
            st.plotly_chart(metric_chart(df, "waterLevel", 50, "AREA"), key="ov_chart")

    # ---- Tab 2: Tanks ----
    with tabs[1]:
        render_tanks(latest)

    # ---- Tab 3: Per-metric charts ----
    with tabs[2]:
        c1, c2 = st.columns([1, 1])
        n = c1.radio("จำนวนจุด", [20, 50, 100, 500], index=1, horizontal=True, key="pm_n")
        kind = c2.radio("รูปแบบกราฟ", ["AREA", "LINE", "BAR"], horizontal=True, key="pm_kind")
        c1, c2, c3 = st.columns(3)
        show_ma = c1.checkbox(f"เส้นค่าเฉลี่ยเคลื่อนที่ ({st.session_state.ma_window} จุด)", value=True, key="pm_ma")
        show_mean = c2.checkbox("เส้นค่าเฉลี่ยรวมของช่วงที่แสดง", value=True, key="pm_mean")
        show_band = c3.checkbox("แถบ ±1 SD", value=False, key="pm_band")
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
                    if k in overlay_pairs and st.checkbox(f"+ {METRICS[overlay_pairs[k]].name_th}", key=f"ov_{k}"):
                        ov = overlay_pairs[k]
                    st.plotly_chart(metric_chart(df, k, n, kind, ov, show_ma, show_mean, show_band),
                                    key=f"pm_{k}")
        st.caption("ปรับเกณฑ์เตือน / จำนวนจุดของค่าเฉลี่ยเคลื่อนที่ ได้ที่แถบด้านซ้าย")

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

    # ---- Tab 5: Statistics ----
    with tabs[4]:
        render_stats(dfw, win_label)

    # ---- Tab 6: Table ----
    with tabs[5]:
        c1, c2, c3 = st.columns([1.3, 1, 1])
        dmin, dmax = raw["timestamp"].min().date(), raw["timestamp"].max().date()
        rng = c1.date_input("ช่วงวันที่", (dmax, dmax), min_value=dmin, max_value=dmax, key="tb_rng")
        only_alert = c2.toggle("เฉพาะแถวที่เกินเกณฑ์", key="tb_alert")
        rows = c3.selectbox("แสดงสูงสุด", [100, 500, 1000, 5000], index=1, key="tb_rows")
        t = raw
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
        st.caption(f"พบ {len(t):,} แถว · แสดง {len(show):,} แถวล่าสุด (ค่าดิบจากชีต)")
        st.dataframe(show, hide_index=True, height=480,
                     column_config={"เวลา": st.column_config.DatetimeColumn(format="YYYY-MM-DD HH:mm:ss")})
        st.download_button("⬇️ ดาวน์โหลด CSV (ตามตัวกรอง)", t.to_csv(index=False).encode("utf-8-sig"),
                           file_name=f"telemetry_{datetime.now(TZ):%Y%m%d_%H%M}.csv", mime="text/csv")

    # ---- Tab 7: Alert log ----
    with tabs[6]:
        log = st.session_state.alert_log
        if not log:
            st.info("ยังไม่มีการแจ้งเตือนตั้งแต่เปิดหน้านี้")
        else:
            st.dataframe(pd.DataFrame(log), hide_index=True)
            if st.button("ล้างบันทึก"):
                st.session_state.alert_log = []


def render_stats(dfw: pd.DataFrame, win_label: str):
    if dfw.empty:
        st.info("ไม่มีข้อมูลในช่วงเวลาที่เลือก")
        return
    t0, t1 = dfw["timestamp"].min(), dfw["timestamp"].max()
    st.markdown(f'<div class="hm-section"><span>สรุปสถิติ — ช่วง {win_label}</span>'
                f'<small>{t0:%Y-%m-%d %H:%M} → {t1:%Y-%m-%d %H:%M} · {len(dfw):,} แถว</small></div>',
                unsafe_allow_html=True)
    limits = {k: thresholds(k)[:2] for k in METRIC_KEYS}
    summary = summary_stats(dfw, limits)
    num = {c: st.column_config.NumberColumn(format="%.2f")
           for c in ["ล่าสุด", "ต่ำสุด", "สูงสุด", "เฉลี่ย", "มัธยฐาน", "SD", "P5", "P95", "Cpk"]}
    num["% เกินเกณฑ์"] = st.column_config.ProgressColumn(format="%.1f%%", min_value=0, max_value=100)
    st.dataframe(summary, hide_index=True, column_config=num)
    st.caption("Cpk = ความสามารถของกระบวนการเทียบกับเกณฑ์เตือน (Min–Max) · ≥ 1.33 ดี · 1.00–1.33 พอใช้ · "
               "< 1.00 ควรปรับปรุง · P5/P95 = 5% และ 95% ของข้อมูลอยู่ต่ำกว่าค่านี้")

    c1, c2 = st.columns([2, 1])
    k = c1.selectbox("พารามิเตอร์", METRIC_KEYS, format_func=lambda x: METRICS[x].name_th, key="st_metric")
    rule_label = c2.selectbox("เฉลี่ยทุก ๆ", list(RESAMPLE_RULES), index=1, key="st_rule")
    left, right = st.columns([1.4, 1])
    with left:
        with st.container(border=True):
            st.markdown(f"**ค่าเฉลี่ยราย {rule_label}** (แถบ = ช่วงต่ำสุด–สูงสุดในแต่ละช่วง)")
            st.plotly_chart(resample_chart(dfw, k, RESAMPLE_RULES[rule_label]), key="st_resample")
    with right:
        with st.container(border=True):
            st.markdown("**การกระจายของค่า** (Histogram)")
            st.plotly_chart(histogram_chart(dfw, k), key="st_hist")


def render_tanks(latest: pd.Series):
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


# รัน dashboard เป็น fragment ที่รีเฟรชอัตโนมัติตามรอบที่ตั้งไว้
st.fragment(run_every=refresh_sec if st.session_state.auto_refresh else None)(dashboard)()
