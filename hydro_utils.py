"""
hydro_utils.py — ฟังก์ชันช่วย (ไม่ขึ้นกับ Streamlit)
- ตั้งค่าเซนเซอร์/เกณฑ์เตือน (แก้ค่าเริ่มต้นได้ที่ METRICS)
- ดึงและแปลงข้อมูลจาก Google Sheets
- สร้าง HTML สำหรับการ์ดและภาพจำลองถัง
"""
from __future__ import annotations

import base64
import io
import math
import re
import struct
import wave
from dataclasses import dataclass
from datetime import datetime
from html import escape

import pandas as pd

# ------------------------------------------------------------------
# 1) ตั้งค่าแหล่งข้อมูล
# ------------------------------------------------------------------
DEFAULT_SHEET_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "19kCwzCFPpCL-Y7iVZ9jZebH41KsS8a4IQyeUJcQwfos/edit?usp=sharing"
)
# gid ของแท็บที่เก็บข้อมูลเซนเซอร์ (ถ้า URL มี #gid=... จะใช้ค่านั้นแทน)
DEFAULT_GID = "1767381986"

# ความสูงถัง (cm) ใช้คำนวณ % ในภาพจำลอง ถ้า None จะใช้ ระยะห่าง + ระดับ
TANK_HEIGHT_CM: float | None = None

# ลำดับคอลัมน์ในชีต (อ่านตามตำแหน่ง ไม่สนชื่อหัวคอลัมน์)
COLUMNS = [
    "timestamp",
    "waterSensorDistance",
    "waterLevel",
    "colorSensorDistance",
    "colorLevel",
    "waterTemperature",
    "remark",
]


# ------------------------------------------------------------------
# 2) ตั้งค่าเซนเซอร์ + เกณฑ์เตือนเริ่มต้น  ← แก้ตรงนี้ได้เลย
# ------------------------------------------------------------------
@dataclass(frozen=True)
class Metric:
    key: str
    name_th: str
    name_en: str
    desc_th: str
    unit: str
    tag: str
    color: str
    default_min: float
    default_max: float
    alert_msg: str
    valid_max: float = 100.0   # ค่าเกินนี้ถือว่าเซนเซอร์อ่านผิด (ใช้เมื่อเปิด "กรองค่าผิดปกติ")
    sound: str = "beep"        # เสียงเตือน: "siren" (ด่วน) | "beep" | "chime" (เบา)
    severity: str = "warning"  # ระดับความรุนแรงใน log: "critical" | "warning"


METRICS: dict[str, Metric] = {
    m.key: m
    for m in [
        Metric("waterSensorDistance", "ระยะห่างเซนเซอร์วัดผิวน้ำ", "Water Sensor Distance",
               "ระยะห่างจากหัวเซนเซอร์ถึงระดับผิวน้ำ", "cm", "SENSOR_01", "#0ea5e9",
               15.0, 28.0, "ระยะเซนเซอร์วัดระดับน้ำผิดปกติ", valid_max=50, sound="beep"),
        Metric("waterLevel", "ระดับความสูงของน้ำ", "Water Level",
               "ระดับความสูงของน้ำจริงภายในถัง", "cm", "LEVEL_CALC", "#3b82f6",
               4.5, 25.0, "ระดับน้ำต่ำหรือสูงเกินเกณฑ์ปลอดภัย!", valid_max=40, sound="siren", severity="critical"),
        Metric("colorSensorDistance", "ระยะห่างเซนเซอร์สี", "Color Sensor Distance",
               "ระยะห่างจากเซนเซอร์แสงถึงผิวน้ำยา/สารละลายสี", "cm", "COLOR_01", "#a855f7",
               15.0, 25.0, "ระยะเซนเซอร์วัดสีผิดปกติ", valid_max=50, sound="beep"),
        Metric("colorLevel", "ระดับสารละลายสี", "Color Level",
               "ระดับความสูงของสารละลายสี", "cm", "SAT_INDEX", "#ec4899",
               5.0, 15.0, "ระดับสารละลายสีไม่ได้มาตรฐาน", valid_max=40, sound="chime"),
        Metric("waterTemperature", "อุณหภูมิน้ำ", "Water Temperature",
               "อุณหภูมิของน้ำแบบเรียลไทม์", "°C", "THERMAL_01", "#f97316",
               22.0, 35.0, "อุณหภูมิน้ำสูงหรือต่ำเกินกำหนด!", valid_max=60, sound="siren", severity="critical"),
    ]
}
METRIC_KEYS = list(METRICS.keys())


# ------------------------------------------------------------------
# 2.1) เสียงแจ้งเตือน (สร้างไฟล์ WAV ในโค้ด ไม่ต้องมีไฟล์เสียงแยก)
# ------------------------------------------------------------------
SOUND_LABELS = {"siren": "ไซเรน (ด่วน)", "beep": "บี๊บ 3 ครั้ง", "chime": "กริ๊ง (เบา)"}
SOUND_PRIORITY = ["siren", "beep", "chime"]  # ถ้าเกินหลายตัวพร้อมกัน เล่นเสียงที่ด่วนที่สุด


def make_wav_base64(kind: str, rate: int = 16000) -> str:
    """สร้างเสียงเตือนเป็น WAV (base64) ตามชนิด siren / beep / chime"""
    samples: list[float] = []

    def tone(freq_fn, dur, env=lambda t, d: 1.0):
        n = int(rate * dur)
        phase = 0.0
        for i in range(n):
            t = i / rate
            phase += 2 * math.pi * freq_fn(t) / rate
            samples.append(math.sin(phase) * env(t, dur))

    def silence(dur):
        samples.extend([0.0] * int(rate * dur))

    fade = lambda t, d: min(1.0, t / 0.01, (d - t) / 0.01)  # กันเสียงแตกตอนเริ่ม/จบ
    if kind == "siren":
        for _ in range(2):
            tone(lambda t: 650 + 550 * (t / 0.8), 0.8, fade)     # ไล่ขึ้น
            tone(lambda t: 1200 - 550 * (t / 0.8), 0.8, fade)    # ไล่ลง
    elif kind == "chime":
        tone(lambda t: 1046.5, 0.45, lambda t, d: math.exp(-5 * t) * min(1, t / 0.005))
        tone(lambda t: 1318.5, 0.9, lambda t, d: math.exp(-4 * t) * min(1, t / 0.005))
    else:  # beep
        for _ in range(3):
            tone(lambda t: 1000, 0.16, fade)
            silence(0.09)

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(max(-1, min(1, x)) * 30000)) for x in samples))
    return base64.b64encode(buf.getvalue()).decode()


def sound_player_html(b64: str, volume: float, nonce: str) -> str:
    """HTML เล็ก ๆ ที่เล่นเสียงทันทีเมื่อแสดงผล (nonce ทำให้เล่นซ้ำได้ทุกครั้ง)"""
    return f"""<!-- {nonce} --><script>
const a = new Audio("data:audio/wav;base64,{b64}");
a.volume = {max(0.0, min(1.0, volume)):.2f};
a.play().catch(() => {{}});
</script>"""


# ------------------------------------------------------------------
# 3) ข้อมูล
# ------------------------------------------------------------------
def build_csv_url(sheet_url: str) -> str:
    """แปลงลิงก์ Google Sheet ปกติ → ลิงก์ export เป็น CSV"""
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", sheet_url)
    if not m:
        raise ValueError("ลิงก์ Google Sheet ไม่ถูกต้อง")
    gid_m = re.search(r"[#&?]gid=(\d+)", sheet_url)
    gid = gid_m.group(1) if gid_m else DEFAULT_GID
    return f"https://docs.google.com/spreadsheets/d/{m.group(1)}/export?format=csv&gid={gid}"


def parse_telemetry(raw: pd.DataFrame) -> pd.DataFrame:
    """ทำความสะอาดข้อมูล: ตั้งชื่อคอลัมน์, แปลงชนิด, เรียงใหม่สุดก่อน"""
    df = raw.iloc[:, : len(COLUMNS)].copy()
    df.columns = COLUMNS[: df.shape[1]]
    if "remark" not in df:
        df["remark"] = "OK"
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    for k in METRIC_KEYS:
        df[k] = pd.to_numeric(df[k], errors="coerce")  # ช่องว่าง → NaN (ไม่ใช่ 0)
    df["remark"] = df["remark"].fillna("").astype(str).str.strip().replace("", "OK")
    df = df.dropna(subset=["timestamp"]).dropna(subset=METRIC_KEYS, how="all")
    df = df.sort_values("timestamp", ascending=False, kind="stable").reset_index(drop=True)
    return df


def load_sheet(sheet_url: str, local_csv: str | None = None) -> pd.DataFrame:
    src = local_csv or build_csv_url(sheet_url)
    return parse_telemetry(pd.read_csv(src))


def remove_outliers(df: pd.DataFrame) -> pd.DataFrame:
    """ค่า ≤ 0 หรือเกิน valid_max → NaN (ถือว่าเซนเซอร์อ่านผิด)"""
    df = df.copy()
    for k, m in METRICS.items():
        df.loc[(df[k] <= 0) | (df[k] > m.valid_max), k] = float("nan")
    return df


def filter_window(df: pd.DataFrame, minutes: int | None) -> pd.DataFrame:
    """เลือกข้อมูลย้อนหลัง N นาทีนับจากแถวล่าสุด (None = ทั้งหมด)"""
    if minutes is None or df.empty:
        return df
    return df[df["timestamp"] >= df["timestamp"].iloc[0] - pd.Timedelta(minutes=minutes)]


def summary_stats(df: pd.DataFrame, limits: dict[str, tuple[float, float]]) -> pd.DataFrame:
    """สรุปสถิติต่อพารามิเตอร์ + Cpk (ใช้เกณฑ์เตือนเป็น LSL/USL)"""
    rows = []
    for k, m in METRICS.items():
        s = df[k].dropna()
        lo, hi = limits[k]
        n = len(s)
        mean, sd = (s.mean(), s.std()) if n else (float("nan"), float("nan"))
        cpk = min(hi - mean, mean - lo) / (3 * sd) if n > 1 and sd > 0 else float("nan")
        rows.append({
            "พารามิเตอร์": f"{m.name_th} ({m.unit})",
            "จำนวน": n,
            "ล่าสุด": s.iloc[0] if n else None,
            "ต่ำสุด": s.min(), "สูงสุด": s.max(),
            "เฉลี่ย": mean, "มัธยฐาน": s.median(), "SD": sd,
            "P5": s.quantile(.05) if n else None, "P95": s.quantile(.95) if n else None,
            "% เกินเกณฑ์": ((s < lo) | (s > hi)).mean() * 100 if n else None,
            "Cpk": cpk,
        })
    return pd.DataFrame(rows)


def evaluate(value: float, lo: float, hi: float) -> str:
    """คืนค่า 'normal' | 'low' | 'high' | 'nodata'"""
    if value is None or pd.isna(value):
        return "nodata"
    if value < lo:
        return "low"
    if value > hi:
        return "high"
    return "normal"


# ------------------------------------------------------------------
# 4) HTML components
# ------------------------------------------------------------------
def _clean(html: str) -> str:
    """ลบการเว้นบรรทัด/ย่อหน้า เพื่อไม่ให้ Markdown ตีความเป็น code block"""
    return "".join(line.strip() for line in html.splitlines())


def _fmt(v, nd=1) -> str:
    return "—" if v is None or pd.isna(v) else f"{v:.{nd}f}"


def delta_badge(cur, prev) -> str:
    if cur is None or prev is None or pd.isna(cur) or pd.isna(prev):
        return '<span class="hm-delta flat">— 0.0</span>'
    d = cur - prev
    if abs(d) < 0.05:
        return '<span class="hm-delta flat">— 0.0</span>'
    cls, arrow = ("up", "▲") if d > 0 else ("down", "▼")
    return f'<span class="hm-delta {cls}">{arrow} {d:+.1f}</span>'


def cards_grid_html(cards: list[str]) -> str:
    """วางการ์ดทั้งหมดใน CSS grid เดียว → ทุกใบสูงเท่ากัน"""
    return '<div class="hm-grid">' + "".join(cards) + "</div>"


def metric_card_html(m: Metric, series: pd.Series, lo: float, hi: float, stats: dict,
                     stats_label: str = "") -> str:
    cur = series.iloc[0] if len(series) else None
    prev = series.iloc[1] if len(series) > 1 else None
    status = evaluate(cur, lo, hi)
    status_txt = {
        "normal": ("ok", "✓ ปกติ"),
        "low": ("bad", "▼ ต่ำกว่าเกณฑ์"),
        "high": ("bad", "▲ สูงกว่าเกณฑ์"),
        "nodata": ("warn", "ไม่มีข้อมูล"),
    }[status]

    # มินิบาร์ 6 ค่าล่าสุด (เก่า → ใหม่)
    tail = series.iloc[:6][::-1].tolist()
    valid = [v for v in tail if not pd.isna(v)]
    vmin, vmax = (min(valid), max(valid)) if valid else (0, 1)
    span = (vmax - vmin) or 1

    def _h(v):  # สเกลตามช่วง min–max ของ 6 ค่าล่าสุด ให้เห็นความต่าง
        return 10 if pd.isna(v) else 30 + 70 * (v - vmin) / span

    bars = "".join(
        f'<div class="hm-bar" style="height:{_h(v):.0f}%;background:{m.color};'
        f'opacity:{1 if i == len(tail) - 1 else 0.35}"></div>'
        for i, v in enumerate(tail)
    )
    return _clean(f"""
<div class="hm-card {'alert' if status in ('low','high') else ''}" style="--accent:{m.color}">
  <div class="hm-card-head">
    <div><div class="hm-card-title">{escape(m.name_th)}</div>
    <div class="hm-card-desc">{escape(m.desc_th)}</div></div>
    <span class="hm-tag">{m.tag}</span>
  </div>
  <div class="hm-value-row">
    <span class="hm-value">{_fmt(cur)}</span><span class="hm-unit">{m.unit}</span>
    {delta_badge(cur, prev)}
  </div>
  <div class="hm-bars">{bars}</div>
  <div class="hm-stats-label">สถิติ {escape(stats_label)}</div>
  <div class="hm-stats">
    <span>MIN <b>{_fmt(stats['min'])}</b></span>
    <span>AVG <b>{_fmt(stats['avg'])}</b></span>
    <span>MAX <b>{_fmt(stats['max'])}</b></span>
  </div>
  <div class="hm-card-foot">
    <span class="hm-status {status_txt[0]}">{status_txt[1]}</span>
    <span class="hm-range">{lo:g} – {hi:g} {m.unit}</span>
  </div>
</div>""")


def tank_html(title: str, icon: str, sensor_label: str, level, dist, color_from: str,
              color_to: str, accent: str, level_label: str) -> str:
    total = TANK_HEIGHT_CM or ((0 if pd.isna(level) else level) + (0 if pd.isna(dist) else dist))
    pct = 0 if not total or pd.isna(level) else max(0, min(100, level / total * 100))
    fill = max(6, pct)
    return _clean(f"""
<div class="hm-panel" style="--accent:{accent}">
  <div class="hm-panel-head"><span>{icon} {escape(title)}</span>
    <span class="hm-chip">{_fmt(level, 2)} cm</span></div>
  <div class="hm-tank">
    <div class="hm-tank-sensor"><span>◉ {sensor_label}</span><span>Gap: {_fmt(dist, 2)} cm</span></div>
    <div class="hm-tank-air" style="height:{100 - fill}%">
      <div class="hm-beam"></div>
      <div class="hm-beam-label">ระยะห่าง: {_fmt(dist, 2)} cm</div>
    </div>
    <div class="hm-liquid" style="height:{fill}%;background:linear-gradient(to top,{color_to},{color_from})">
      <div class="hm-liquid-label">{escape(level_label)}: {_fmt(level, 2)} cm</div>
    </div>
  </div>
  <div class="hm-tank-foot">เซนเซอร์: <b>{_fmt(dist)}</b> cm · ระดับ: <b style="color:{accent}">{_fmt(level)}</b> cm
   · <span style="color:#64748b">{pct:.0f}%</span></div>
</div>""")


def thermo_html(temp, lo: float, hi: float) -> str:
    pct = 0 if pd.isna(temp) else max(4, min(100, temp / 50 * 100))
    status = evaluate(temp, lo, hi)
    label = {"normal": "ปกติ", "low": "เย็นเกิน", "high": "ร้อนเกิน", "nodata": "—"}[status]
    cls = "ok" if status == "normal" else "bad"
    return _clean(f"""
<div class="hm-panel" style="--accent:#f97316">
  <div class="hm-panel-head"><span>🌡 เกจวัดอุณหภูมิน้ำ</span>
    <span class="hm-status {cls}">{label}</span></div>
  <div class="hm-thermo-wrap">
    <div class="hm-thermo">
      <div class="hm-thermo-fill" style="height:{pct}%"></div>
      <div class="hm-thermo-scale"><span>50°</span><span>40°</span><span>30°</span><span>20°</span><span>10°</span></div>
    </div>
    <div class="hm-thermo-info">
      <div class="hm-thermo-val">{_fmt(temp)}<small> °C</small></div>
      <div class="hm-thermo-row">&gt; {hi:g}°C (ร้อน)</div>
      <div class="hm-thermo-row ok">{lo:g}–{hi:g}°C (ปกติ)</div>
      <div class="hm-thermo-row">&lt; {lo:g}°C (เย็น)</div>
    </div>
  </div>
  <div class="hm-tank-foot">อุณหภูมิน้ำปัจจุบัน: <b style="color:#f97316">{_fmt(temp, 2)} °C</b></div>
</div>""")


def alert_event_html(ev: dict) -> str:
    """การ์ดเหตุการณ์แจ้งเตือน 1 รายการ (ปุ่มกดอยู่ใน app.py)"""
    m = METRICS[ev["metric"]]
    sev = ev["severity"]
    status_cls = {"ใหม่": "new", "รับทราบ": "ack", "แก้ไขแล้ว": "done"}[ev["status"]]
    fmt = lambda t: t.strftime("%Y-%m-%d %H:%M:%S")
    dur = ev["last"] - ev["start"]
    mins, secs = divmod(int(dur.total_seconds()), 60)
    if ev["normal_at"] is not None:
        state = f'<span class="hm-ev-normal">✓ กลับสู่ปกติ {fmt(ev["normal_at"])}</span>'
    elif ev["status"] == "แก้ไขแล้ว":
        state = ""
    else:
        state = '<span class="hm-ev-live">● ยังเกินเกณฑ์</span>'
    status_line = ""
    if ev["status"] != "ใหม่":
        status_line = (f'<div class="hm-ev-status">{ev["status"]} เมื่อ {fmt(ev["status_at"])}'
                       + (f' · หมายเหตุ: {escape(ev["note"])}' if ev.get("note") else "") + "</div>")
    return _clean(f"""
<div class="hm-ev-head">
  <span class="hm-sev {sev}">{sev.upper()}</span>
  <b class="hm-ev-name">{escape(m.name_th)}</b>
  <span class="hm-ev-status-pill {status_cls}">{ev["status"]}</span>
  <span class="hm-ev-time">🕒 {fmt(ev["start"])}</span>
</div>
<div class="hm-ev-msg">{escape(m.alert_msg)} ({ev["first_value"]:.2f} {m.unit})</div>
<div class="hm-ev-foot">
  <span>Trigger: <b>{ev["first_value"]:.2f} {m.unit}</b> <i>({escape(ev["cond"])})</i></span>
  <span>ล่าสุด <b>{ev["last_value"]:.2f}</b> · หนักสุด <b>{ev["worst_value"]:.2f}</b> · {ev["count"]} ครั้ง · {mins} นาที {secs} วินาที</span>
  {state}
</div>
{status_line}""")


def header_html(n_rows: int, latest_ts, refresh_label: str, fetched_at: datetime,
                is_fresh: bool, sheet_url: str, alerts_new: int = 0) -> str:
    dot = "#10b981" if is_fresh else "#f59e0b"
    state = "ออนไลน์" if is_fresh else "ไม่มีข้อมูลใหม่"
    ts = latest_ts.strftime("%Y-%m-%d %H:%M:%S") if latest_ts is not None and not pd.isna(latest_ts) else "—"
    return _clean(f"""
<div class="hm-header">
  <div class="hm-brand">
    <div class="hm-title">ระบบโทรมาตร <b>HYDROMETRICS</b> <small>v3.0 · Python</small></div>
    <div class="hm-sub">ดึงข้อมูลเรียลไทม์จาก Google Sheets พร้อมระบบแจ้งเตือนแยกพารามิเตอร์</div>
  </div>
  <div class="hm-meta">
    <span class="hm-badge">LIVE_SYNC</span>
    <div><small>SOURCE</small><b class="cyan">GOOGLE SHEETS</b></div>
    <div><small>REFRESH</small><b>{refresh_label}</b></div>
    <div><small>LAST FETCH</small><b>{fetched_at:%H:%M:%S}</b></div>
    <div><small>LATEST DATA</small><b>{ts}</b></div>
    <span class="hm-online"><i style="background:{dot}"></i>{state} | {n_rows:,} แถว</span>
    {f'<span class="hm-bell">🔔 {alerts_new} ใหม่</span>' if alerts_new else '<span class="hm-bell zero">🔔 0</span>'}
    <a class="hm-link" href="{escape(sheet_url)}" target="_blank">เปิดชีต ↗</a>
  </div>
</div>""")


CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+Thai:wght@400;600;700&family=JetBrains+Mono:wght@400;700&display=swap');
html, body, [class*="css"], .stMarkdown, .stTabs, button, input, label { font-family: 'IBM Plex Sans Thai', sans-serif; }
.block-container { padding-top: 1.2rem; max-width: 1600px; }
.hm-header { display:flex; flex-wrap:wrap; gap:16px; justify-content:space-between; align-items:center;
  border-bottom:1px solid #1e293b; padding-bottom:14px; margin-bottom:8px; }
.hm-title { font-size:1.35rem; color:#e2e8f0; letter-spacing:.04em; }
.hm-title b { font-family:'JetBrains Mono',monospace; letter-spacing:.12em; }
.hm-title small { color:#64748b; font-size:.75rem; font-family:'JetBrains Mono',monospace; }
.hm-sub { color:#64748b; font-size:.8rem; }
.hm-meta { display:flex; flex-wrap:wrap; gap:18px; align-items:center; font-family:'JetBrains Mono',monospace; }
.hm-meta div { display:flex; flex-direction:column; line-height:1.2; }
.hm-meta small { color:#64748b; font-size:.62rem; letter-spacing:.08em; }
.hm-meta b { color:#e2e8f0; font-size:.78rem; } .hm-meta b.cyan { color:#22d3ee; }
.hm-badge { border:1px solid #155e75; background:#083344; color:#22d3ee; padding:3px 10px; border-radius:6px; font-size:.72rem; font-weight:700; }
.hm-online { border:1px solid #1e293b; background:#0b1220; padding:6px 12px; border-radius:8px; color:#cbd5e1; font-size:.78rem; }
.hm-online i { display:inline-block; width:9px; height:9px; border-radius:50%; margin-right:7px; box-shadow:0 0 8px currentColor; }
.hm-bell { background:#e11d48; color:#fff; font-weight:700; font-size:.78rem; padding:5px 12px; border-radius:999px;
  box-shadow:0 0 14px -2px #e11d48; animation:hm-pulse 1.6s infinite; }
.hm-bell.zero { background:#0f172a; color:#64748b; box-shadow:none; animation:none; border:1px solid #1e293b; }
@keyframes hm-pulse { 50% { opacity:.65; } }
/* การ์ดเหตุการณ์แจ้งเตือน (ใช้ key ของ st.container → class st-key-...) */
div[class*="st-key-evc-"], div[class*="st-key-evw-"], div[class*="st-key-evd-"] {
  border-radius:12px; padding:14px 16px; gap:.4rem; }
div[class*="st-key-evc-"] { border:1px solid #be123c; background:#1a0710; }
div[class*="st-key-evw-"] { border:1px solid #b45309; background:#170f06; }
div[class*="st-key-evd-"] { border:1px solid #1e293b; background:#0b1120; opacity:.75; }
.hm-ev-head { display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
.hm-sev { font-family:'JetBrains Mono',monospace; font-size:.68rem; font-weight:700; padding:2px 8px; border-radius:5px; }
.hm-sev.critical { background:#e11d48; color:#fff; } .hm-sev.warning { background:#f59e0b; color:#1c1917; }
.hm-ev-name { color:#f1f5f9; font-size:.95rem; }
.hm-ev-time { margin-left:auto; color:#94a3b8; font-family:'JetBrains Mono',monospace; font-size:.75rem; }
.hm-ev-status-pill { font-size:.68rem; padding:1px 8px; border-radius:999px; border:1px solid; }
.hm-ev-status-pill.new { color:#fb7185; border-color:#9f1239; } .hm-ev-status-pill.ack { color:#38bdf8; border-color:#075985; }
.hm-ev-status-pill.done { color:#34d399; border-color:#065f46; }
.hm-ev-msg { color:#e2e8f0; font-weight:600; font-size:.88rem; margin:6px 0; }
.hm-ev-foot { display:flex; flex-wrap:wrap; gap:6px 18px; border-top:1px solid #ffffff14; padding-top:8px;
  font-family:'JetBrains Mono',monospace; font-size:.72rem; color:#94a3b8; }
.hm-ev-foot b { color:#f8fafc; } .hm-ev-foot i { color:#64748b; font-style:normal; }
.hm-ev-live { color:#fb7185; } .hm-ev-normal { color:#34d399; }
.hm-ev-status { color:#64748b; font-size:.72rem; margin-top:6px; }
.hm-banner { display:flex; align-items:center; gap:12px; background:#1a0710; border:1px solid #be123c; border-radius:10px;
  padding:10px 14px; color:#fecdd3; font-size:.85rem; margin:6px 0; }
.hm-link { color:#22d3ee !important; font-size:.8rem; text-decoration:none; }
.hm-section { color:#f1f5f9; font-weight:700; font-size:1.15rem; margin:10px 0 6px; display:flex; justify-content:space-between; align-items:center; }
.hm-section::before { content:''; width:10px; height:10px; border-radius:50%; background:#22d3ee; margin-right:10px; display:inline-block; }
.hm-section span { flex:1; } .hm-section small { color:#94a3b8; font-family:'JetBrains Mono',monospace; font-weight:400; font-size:.8rem; }

.hm-grid { display:grid; grid-template-columns:repeat(auto-fit, minmax(230px, 1fr)); gap:14px; align-items:stretch; }
.hm-card { background:#0b1120; border:1px solid #1e293b; border-left:3px solid var(--accent); border-radius:12px;
  padding:16px 16px 12px; display:flex; flex-direction:column; gap:10px; }
.hm-card.alert { box-shadow:0 0 0 1px #7f1d1d inset, 0 0 18px -6px #ef4444; }
.hm-card-head { display:flex; justify-content:space-between; gap:8px; align-items:flex-start; flex:1 0 auto; }
.hm-card-title { color:#e2e8f0; font-weight:700; font-size:.95rem; line-height:1.3; }
.hm-card-desc { color:#64748b; font-size:.72rem; line-height:1.35; margin-top:2px; }
.hm-tag { font-family:'JetBrains Mono',monospace; font-size:.62rem; font-weight:700; color:var(--accent);
  border:1px solid color-mix(in srgb, var(--accent) 45%, transparent); background:color-mix(in srgb, var(--accent) 12%, transparent);
  padding:2px 7px; border-radius:5px; white-space:nowrap; }
.hm-value-row { display:flex; align-items:center; gap:6px; flex-wrap:nowrap; min-height:3rem; }
.hm-value { font-family:'JetBrains Mono',monospace; font-size:clamp(1.9rem, 2.3vw, 2.6rem); font-weight:700; color:#f8fafc; line-height:1; }
.hm-unit { color:#64748b; font-size:1.05rem; font-family:'JetBrains Mono',monospace; margin-right:auto; }
.hm-delta { font-family:'JetBrains Mono',monospace; font-size:.72rem; font-weight:700; padding:2px 7px; border-radius:6px; }
.hm-delta.up { color:#34d399; background:#022c22; border:1px solid #065f46; }
.hm-delta.down { color:#fb7185; background:#2a0a12; border:1px solid #881337; }
.hm-delta.flat { color:#94a3b8; background:#0f172a; border:1px solid #1e293b; }
.hm-bars { display:flex; gap:6px; align-items:flex-end; height:22px; border-top:1px solid #1e293b; padding-top:8px; }
.hm-bar { flex:1; border-radius:2px; }
.hm-stats { display:flex; justify-content:space-between; font-family:'JetBrains Mono',monospace; font-size:.66rem; color:#64748b; }
.hm-stats b { color:#cbd5e1; }
.hm-stats-label { font-size:.6rem; color:#475569; letter-spacing:.06em; margin-bottom:-6px; }
.hm-delta { white-space:nowrap; flex-shrink:0; }
.hm-card-foot { display:flex; justify-content:space-between; align-items:center; font-size:.72rem; }
.hm-range { color:#64748b; font-family:'JetBrains Mono',monospace; }
.hm-status { font-size:.72rem; font-weight:700; padding:2px 8px; border-radius:6px; }
.hm-status.ok { color:#34d399; background:#022c22; border:1px solid #065f46; }
.hm-status.bad { color:#fb7185; background:#2a0a12; border:1px solid #881337; }
.hm-status.warn { color:#fbbf24; background:#2a1a04; border:1px solid #78350f; }

.hm-panel { background:#0a0e17; border:1px solid #1e293b; border-radius:12px; padding:14px; height:100%; }
.hm-panel-head { display:flex; justify-content:space-between; align-items:center; color:var(--accent); font-weight:700; font-size:.92rem; margin-bottom:10px; }
.hm-chip { font-family:'JetBrains Mono',monospace; font-size:.72rem; color:var(--accent); border:1px solid #1e293b; background:#0f172a; padding:2px 8px; border-radius:6px; }
.hm-tank { position:relative; height:280px; max-width:240px; margin:0 auto; background:#0f172a; border:2px solid #1e3a5f;
  border-radius:12px; overflow:hidden; display:flex; flex-direction:column; }
.hm-tank-sensor { display:flex; justify-content:space-between; font-family:'JetBrains Mono',monospace; font-size:.62rem;
  color:#94a3b8; background:#1e293b; padding:4px 8px; }
.hm-tank-sensor span:first-child { color:var(--accent); }
.hm-tank-air { position:relative; display:flex; justify-content:center; transition:height .6s; }
.hm-beam { width:0; border-left:2px dashed var(--accent); opacity:.7; height:100%; }
.hm-beam-label { position:absolute; top:50%; transform:translateY(-50%); font-family:'JetBrains Mono',monospace; font-size:.62rem;
  color:var(--accent); background:#020617cc; border:1px solid #334155; padding:2px 6px; border-radius:4px; white-space:nowrap; }
.hm-liquid { margin-top:auto; position:relative; display:flex; align-items:flex-end; justify-content:center; transition:height .6s;
  box-shadow: inset 0 3px 0 rgba(255,255,255,.35); }
.hm-liquid-label { font-family:'JetBrains Mono',monospace; font-size:.68rem; color:#fff; font-weight:700; padding:4px; text-shadow:0 1px 2px #000; }
.hm-tank-foot { text-align:center; color:#94a3b8; font-size:.78rem; margin-top:10px; font-family:'JetBrains Mono',monospace; }
.hm-tank-foot b { color:#e2e8f0; }
.hm-thermo-wrap { display:flex; gap:20px; align-items:center; justify-content:center; height:280px;
  border:1px solid #7c2d12; border-radius:12px; background:#140c08; }
.hm-thermo { position:relative; width:26px; height:220px; background:#1e293b; border-radius:14px; border:2px solid #334155; overflow:hidden; }
.hm-thermo-fill { position:absolute; bottom:0; left:0; right:0; background:linear-gradient(to top,#10b981,#f59e0b,#ef4444); border-radius:0 0 12px 12px; }
.hm-thermo-scale { position:absolute; left:32px; top:0; bottom:0; display:flex; flex-direction:column; justify-content:space-between; }
.hm-thermo-scale span { font-size:.55rem; color:#64748b; font-family:'JetBrains Mono',monospace; }
.hm-thermo-info { display:flex; flex-direction:column; gap:8px; margin-left:18px; }
.hm-thermo-val { font-family:'JetBrains Mono',monospace; font-size:2.2rem; font-weight:700; color:#fff; }
.hm-thermo-val small { color:#f97316; font-size:1rem; }
.hm-thermo-row { font-size:.72rem; color:#64748b; font-family:'JetBrains Mono',monospace; }
.hm-thermo-row.ok { color:#34d399; border:1px solid #065f46; background:#022c22; padding:4px 8px; border-radius:6px; }

.stTabs [data-baseweb="tab-list"] { gap:8px; }
.stTabs [data-baseweb="tab"] { background:#0b1120; border:1px solid #1e293b; border-radius:8px; padding:6px 16px; }
.stTabs [aria-selected="true"] { background:#0891b2 !important; color:#fff !important; border-color:#22d3ee; }
.stTabs [data-baseweb="tab-highlight"], .stTabs [data-baseweb="tab-border"] { display:none; }
</style>
"""
