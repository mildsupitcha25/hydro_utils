# HYDROMETRICS — Water & Color IoT Telemetry Dashboard (Streamlit)

Dashboard อ่านค่าเซนเซอร์จาก Google Sheets แบบเรียลไทม์ เขียนด้วย Python ล้วน

## โครงสร้างไฟล์
| ไฟล์ | ใช้ทำอะไร |
|---|---|
| `app.py` | หน้าจอ Dashboard (แท็บ, กราฟ, ตาราง, แจ้งเตือน) |
| `hydro_utils.py` | **ค่าที่แก้บ่อยอยู่ที่นี่** — ลิงก์ชีต, เกณฑ์เตือนเริ่มต้น, สี, ชื่อเซนเซอร์, หน้าตาการ์ด (CSS) |
| `requirements.txt` | ไลบรารีที่ต้องติดตั้ง |
| `.streamlit/config.toml` | ธีมสีเข้ม |

## รันในเครื่อง
```bash
pip install -r requirements.txt
streamlit run app.py
```
เปิดเบราว์เซอร์ที่ http://localhost:8501

## ขึ้นเว็บด้วย Streamlit Community Cloud (ฟรี)
1. ตั้งแชร์ Google Sheet เป็น **ทุกคนที่มีลิงก์ → ผู้มีสิทธิ์อ่าน** (ทำครั้งเดียว)
2. สร้าง repo บน GitHub แล้วอัปโหลดไฟล์ทั้งหมดในโฟลเดอร์นี้ (รวมโฟลเดอร์ `.streamlit`)
3. ไปที่ https://share.streamlit.io → **Create app** → เลือก repo, branch `main`, ไฟล์ `app.py` → **Deploy**
4. (ไม่บังคับ) ถ้าอยากเปลี่ยนชีตโดยไม่แก้โค้ด: App → Settings → Secrets ใส่
   ```toml
   SHEET_URL = "https://docs.google.com/spreadsheets/d/xxxx/edit#gid=1767381986"
   ```
5. ตั้ง App visibility เป็น Public เพื่อให้คนอื่นเปิดดูได้โดยไม่ต้องขอสิทธิ์

แก้โค้ดบน GitHub แล้วกด commit → เว็บอัปเดตเองภายในไม่กี่วินาที

## รูปแบบข้อมูลในชีต
อ่านตาม **ลำดับคอลัมน์** (ชื่อหัวคอลัมน์เปลี่ยนได้): 
`Timestamp | Water Sensor Distance | Water Level | Color Sensor Distance | Color Level | Water Temperature | Remark`
แถวใหม่อยู่บนหรือล่างก็ได้ — โปรแกรมเรียงตามเวลาให้เอง ช่องว่างจะไม่ถูกนับเป็น 0

## แก้ไขที่พบบ่อย
- **เปลี่ยนเกณฑ์เตือนเริ่มต้น** → `hydro_utils.py` ส่วน `METRICS` (ค่า `default_min`, `default_max`)
- **เปลี่ยนแท็บของชีต** → `DEFAULT_GID` หรือใส่ลิงก์ที่มี `#gid=...`
- **กำหนดความสูงถังจริง** → `TANK_HEIGHT_CM` (ค่าเริ่มต้นคำนวณจาก ระยะห่าง + ระดับ)
- **ปรับเกณฑ์ชั่วคราว** → แถบด้านซ้ายของหน้าเว็บ (ใช้เฉพาะหน้าจอนั้น)

## ทดสอบกับไฟล์ CSV ในเครื่อง
```bash
HYDRO_CSV=sample.csv streamlit run app.py
```
