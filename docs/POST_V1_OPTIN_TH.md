# ฟีเจอร์เสริมหลัง v1 (opt-in) — LAN / Autostart / ชั้น B / Wake word

ทั้งสี่อย่าง **ปิดเป็นค่าเริ่มต้น** ไม่มีอะไรทำงานโดยอัตโนมัติ ต้องสั่งเปิดเองทีละข้อ และปิดกลับได้ด้วยการลบ/ไม่ใส่แฟล็กเดิม
เอกสารนี้คือคู่มือใช้งาน + หลักฐานที่ตรวจแล้ว (วันที่ 10 ต.ค. 2569)

## 1) เปิดให้เข้าถึงจาก LAN (opt-in)

```bash
python3 start-thai.py --no-browser                      # ปกติ: 127.0.0.1 เท่านั้น (ค่าเริ่มต้น)
python3 start-thai.py --no-browser --lan                # ฟังทุก interface (0.0.0.0)
python3 start-thai.py --no-browser --lan --lan-allow "192.168.31.69,192.168.31.70"
```

- Host/Origin ต้องเป็น `127.0.0.1`, `localhost` หรือ IP ของเครื่องนี้เท่านั้น — Host ปลอมได้ 403
- มี `--lan-allow` จะจำกัดเฉพาะ IP ที่ระบุ (ไม่มี prefix matching, ไม่รับค่าที่ไม่ใช่ IP) และ **localhost ไม่ถูกตัดออก** เสมอ
- Token ยังถูกบังคับทุกคำขอ (ไม่มี token = 401)
- ข้อควรระวัง: หน้าเว็บมี token ของรอบนั้นอยู่แล้ว ดังนั้นใครก็ตามที่เข้าถึงพอร์ตนี้ในเครือข่ายเดียวกันสามารถใช้ระบบได้ — เปิดเฉพาะเครือข่ายที่ไว้ใจได้

**หลักฐานจริง (พอร์ตทดสอบ 8740, allowlist = 192.168.31.69):**

| คำขอ | ผลลัพธ์ |
|---|---|
| loopback → `/api/status` | 200 |
| ไคลเอนต์ LAN จริง (`--interface 192.168.31.69`) + Host ถูกต้อง | 200 |
| ไคลเอนต์ LAN + Host ปลอม `evil.example:8740` | 403 |
| POST `/api/speak` จาก LAN โดยไม่มี token | 401 |
| การ bind | `*:8740` (ทุก interface) เฉพาะตอนเปิด `--lan` — ค่าเริ่มต้นยัง `127.0.0.1:8730` |

## 2) Autostart เมื่อเข้าสู่ระบบ (opt-in, macOS LaunchAgent)

```bash
python3 autostart.py status        # ตรวจก่อน
python3 autostart.py install       # เขียน ~/Library/LaunchAgents/com.jarvis.thai-voice.plist
python3 autostart.py install --load   # เขียน + สั่ง launchctl bootstrap ทันที
python3 autostart.py uninstall     # ลบออก
```

- ต้องสั่งเองทุกครั้ง ไม่มีการติดตั้งเงียบ ๆ
- ใน plist มีเฉพาะ path/พอร์ต — **ไม่มี token/secrets** เพราะ server สร้าง token ใหม่ทุกครั้งที่เริ่ม
- `KeepAlive=false` คือรันครั้งเดียวต่อการล็อกอิน (ไม่รีสตาร์ตอัตโนมัติ)
- ผลล็อกอยู่ที่ `autostart.log` ในโฟลเดอร์โปรเจกต์

## 3) ชั้น B: เขียนไฟล์ / รันคำสั่ง (opt-in, sandbox + audit + อนุมัติรายครั้ง)

แก้ `tools_layer_b.json`:

```json
{
  "enabled": true,
  "sandbox_root": "/Volumes/Data_Sufayan/jarvis_sandbox",
  "allow_commands": ["/bin/ls", "/usr/bin/env", "/usr/bin/python3"],
  "max_write_bytes": 100000,
  "max_output_bytes": 20000,
  "timeout_seconds": 20,
  "audit_log": ""
}
```

กติกาที่บังคับในโค้ด (ทดสอบแล้ว):

- เครื่องมือ `write_text_file` / `run_command` จะปรากฏต่อโมเดล **เฉพาะเมื่อ enabled:true และ sandbox มีอยู่จริง**
- ทุกครั้งยังต้องผ่านกลไกอนุมัติสองระยะเดิม (digest ผูก action+args+request, หมดอายุ 90 วิ, ใช้ได้ครั้งเดียว, ผูก client) — worker ไม่เคย execute เอง
- เขียน: ใช้ path **สัมพัทธ์** เท่านั้น ต้อง resolve อยู่ใน sandbox หลังแก้ symlink; `..`, path เต็ม และ symlink หนีขอบ = ปฏิเสธ; ขนาดเกิน cap = 413
- รัน: argv ผ่าน `shell=False` (อักขระพิเศษเป็นข้อความธรรมดา) cwd ผูกกับ sandbox, timeout, ตัด output, ตัด env ที่มีชื่อเหมือน secret
- ทุกครั้งเขียน 1 บรรทัด JSON ลง audit log (action, argv/path, exit code, bytes, sha256) — **ไม่บันทึกเนื้อหาไฟล์** และถ้า audit log เขียนไม่ได้ = ไม่ดำเนินการอะไรเลย
- ปิดกลับ: ตั้ง `"enabled": false` (หรือลบไฟล์) → เครื่องมือหายไปจากโมเดลทันที

## 4) Wake word “จาร์วิส” (opt-in)

```bash
python3 start-thai.py --no-browser     # server ต้องรันก่อน
python3 wakeword.py                    # สคริปต์นี้ทำงานเอง ไม่มีใครสั่งให้รันอัตโนมัติ
python3 wakeword.py --device 1         # เลือกไมค์ตาม index ของ sounddevice
```

- วลีปลุก: `จาร์วิส`, `จาวิส`, `จารวิส`, `JARVIS` (ตรงกับการสะกดที่มักได้จากถอดเสียง)
- ทำงานบน loopback เท่านั้น ใช้ token/Host/Origin ชุดเดียวกับเบราว์เซอร์ และยิงผ่าน `/api/listen` (ถอดเสียงในเครื่อง) กับ `/api/run` — **ไม่มีเสียงออกจากเครื่อง** และไม่บันทึกเสียง
- ถ้าคำสั่งเรียกใช้เครื่องมือ read-only/ชั้น B ระบบจะยังขึ้นแผง “ต้องการอนุมัติ” ตามเดิม — เสียงปลุกไม่ใช่สิทธิ์
- Ctrl-C เพื่อหยุด

## การทดสอบ

- `python3 -m pytest tests/ -q` → **86 passed** (เพิ่มจาก 50: layer B 19 + LAN/autostart 8 + wake word 9 กรณี)
- Node UI tests ผ่านทั้ง 5 ชุด, `git diff --check` ผ่าน
- ตารางหลักฐาน LAN จริงอยู่ด้านบน

## ย้อนกลับ

| ฟีเจอร์ | วิธีปิด |
|---|---|
| LAN | ตัด `--lan` / `--lan-allow` ออก (หรือ unset `JARVIS_LAN`) |
| Autostart | `python3 autostart.py uninstall` |
| ชั้น B | `tools_layer_b.json` → `"enabled": false` |
| Wake word | Ctrl-C (หรือเลิกสั่ง `python3 wakeword.py`) |
