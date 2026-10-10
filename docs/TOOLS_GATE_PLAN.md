# แผนเปิดเครื่องมือแบบจำกัด (C1) — **ชั้น A (read-only) เปิดใช้แล้ว**

> เปิดใช้จริงเมื่อ 2026-10-10 โดย `tools_gate.json` (`enabled: true`) — ปิดกลับเป็น chat-only ทันทีด้วยการตั้ง `enabled: false`

## 0. สิ่งที่ส่งมอบแล้ว (หลักฐานจริง)
- `tools_readonly.py` — เครื่องมือเป็นของ pilot เอง 2 ตัว: `read_text_file`, `search_text` (path ต้อง resolve อยู่ใน allowlist, symlink ที่ชี้ออกนอกรากถูกปฏิเสธ, ขนาด/จำนวนผลลัพธ์ถูกจำกัด, ไฟล์ไบนารีถูกปฏิเสธตอน execute)
- `agent_worker.py` — โมเดลเห็น schema ทั้งสอง แต่ **ไม่มีการรันใน worker**: เรียกเครื่องมือ = หยุดเทิร์น แล้วคืน approval request (digest + หมดอายุ 90 วิ) ผ่าน instance-local gate ที่ abort ก่อน native executor เสมอ
- `agent_bridge.py` — pending ผูกกับ chat `client_id`+`request_id` (UUID), digest ด้วย `hmac.compare_digest`, ใช้ครั้งเดียว, หมดอายุ 410, ผิด owner 404, ผิด digest 403; หลังอนุมัติจึง execute และส่งผลจริงกลับให้โมเดลตอบในเทิร์นถัดไป
- `server.py` — `/api/approve`, `/api/deny` ภายใต้ Host/Origin/token/schema guards เดิม
- UI — แผง “ต้องการอนุมัติการอ่านไฟล์” แสดงการกระทำ+arguments+หมดอายุ ปุ่ม **อนุมัติอ่านครั้งนี้** / **ปฏิเสธ**

ผลทดสอบจริง (ทุกอันรันบนเครื่องนี้):
- HTTP+LLM จริง: ส่งคำถามให้อ่าน `docs/QUICKSTART_TH.md` → ได้ approval (path ถูกต้อง) → digest ปลอม **403** (ไม่ consume) → อนุมัติจริง → อ่านไฟล์ 3,566 bytes → ตอบตามเนื้อหาจริง
- HTTP+LLM จริง: ขอให้อ่าน `/etc/hosts` → **403 "path อยู่นอกโฟลเดอร์ที่อนุญาต"** ทันที ไม่มีการอ่านไฟล์ใด ๆ
- Chrome จริง (คลิก trusted): พิมพ์คำถาม → แผงอนุมัติแสดง `read_text_file` + `ROLLBACK.md` + เวลาหมดอายุ → กดอนุมัติ → คำตอบอิงไฟล์จริง, แผงปิด, ปุ่มอ่านเปิด, ไม่มี JS error
- `tests/test_tools_gate.py` 15 tests (path escape/symlink/size/binary/regex/limit/digest/one-shot/expiry/owner/endpoints) · ชุดเต็ม Python **48 passed**, Node **26 passed**

## 1. ขอบเขตที่พิสูจน์แล้วตอนนี้
- Chat-only: `agent_worker.py` สร้าง `AIAgent` ด้วย `enabled_toolsets=[]`, `skip_memory/skip_background_review/skip_context_files`, `session_db=None` แล้วตรวจ `tools==[]` และ `valid_tool_names==set()` หลัง init
- **หลักฐานสำคัญ:** native dispatcher ไม่ใช่ตัวกัน — ก่อนแก้ มีการยิง forged `terminal` call กับ agent ที่ tool schema ว่างแล้ว พบว่า **native executor ยังรัน** แล้วค่อยถูกปฏิเสธเพราะ argument ผิด (`Invalid command: expected string, got NoneType`) → จึงติด instance-local fail-closed gates (`_execute_tool_calls`, sequential/concurrent, `_invoke_tool`, `_dispatch_delegate_task` → `ToolAttempt`) ก่อนส่งคำสั่งใด ๆ ถึงโมเดล และ exercise gates 6 ชื่อใน readiness proof ทุกครั้ง
- ผลทดสอบ: `tests/test_agent_bridge.py` (gates/replay/isolation/429-mapping), `tests/test_phase4_resilience.py` (restart, contention, guard matrix, temp/auth/config), `tests/agent_discovery/test_approval_transport_proof.py` (4/4: once-only, deny, expired, digest-mismatch)

## 2. ทำไมยังเปิดเครื่องมือไม่ได้
ไม่มี **universal pre-dispatch approval hook** ใน Hermes: `set_approval_callback` คุมเฉพาะ terminal ที่จัดว่าเสี่ยง ไม่ครอบคลุม write_file/email/browser/delegation ส่วน plugin hooks ที่มีเป็น fail-open (ข้ามได้ถ้าไม่มี handler) ดังนั้นถ้าเปิด tool ตอนนี้ จะอ้างไม่ได้ว่าทุก side effect ผ่านการยืนยัน

## 3. สถาปัตยกรรมที่เสนอ (อ่านอย่างเดียวก่อน → เขียน/สั่งทีหลัง)

### 3.1 ข้อตกลง: two-phase approval ผูก digest + หมดอายุ
```
รัน (phase A)                      ยืนยัน (phase B)
────────────────────               ─────────────────────
worker สร้าง agent + gates         server เก็บ pending:
แล้วผูก tool ที่อนุญาตเท่านั้น         {request_id, digest, expires_at, owner_client, action}
ถ้าโมเดลเรียก tool ที่อนุญาต          → UI แสดง action+argument เต็ม
worker หยุดและคืน                  → ผู้ใช้กดยืนยัน (ตัวนับถอยหลัง)
{approval_request}                 server ตรวจ digest+owner+expiry ทีละขั้น
ปิด process ไม่ทำอะไรทั้งสิ้น        ถ้าผ่าน → เรียก phase B worker ใหม่ด้วย token ใช้ครั้งเดียว
                                   ถ้าไม่ผ่าน → 404/410/409 fail-closed
```
- `digest` = SHA-256 ของ `{action, args, request_id}` → เปลี่ยน argument แม้ 1 ตัวอักษร = ยืนยันไม่ผ่าน
- `expires_at` = 90 วินาที; หมดอายุ = ต้องเริ่มรันใหม่
- ใช้กลไกที่มีอยู่จริง `ApprovalRequest.create` + `invoke_approval_transport` (มี request id/digest/timeout/fail-closed ตาม proof ที่รันแล้ว) ไม่เขียน approval ของตัวเองทับ
- ทุกอย่าง bound กับ `client_id` + `request_id` เฉกเช่นแชต

### 3.2 ลำดับเปิด (จำกัดทีละชั้น)
1. **ชั้น A — อ่านอย่างเดียว:** `read_file` (path ต้องอยู่ใน allowlist ที่ระบุ) / `search_files` — ไม่มีการเขียน ไม่มี network
2. **ชั้น B — เครื่องมือเขียน/คำสั่ง:** ยัง **ไม่เสนอ** จนกว่าชั้น A จะผ่านการใช้งานจริงโดยไม่มี incident และมี sandbox fixture ยืนยันว่า action ที่ไม่ได้รับอนุมัติไม่เกิดผล
3. ทุกชั้น: `/api/jobs`, delegation, cron, messaging, browser — ปิดถาวรใน pilot นี้

### 3.3 เกณฑ์ต้องผ่านก่อนเปิดชั้น A
- [ ] proof ว่า tool surface ที่อนุญาตเป็นไปตาม allowlist เท่านั้น (รายการชื่อ ตรวจสอบหลัง init)
- [ ] forged call นอก allowlist → abort ก่อนถึง registry (มี trap test)
- [ ] pending approval: digest/owner/expiry/once-only มี test ครบทุกกรณี
- [ ] ยกเลิก/หมดอายุ/เปลี่ยน argument → ไม่มีผลใด ๆ เกิดขึ้น
- [ ] log/audit: บันทึก action, digest, ผล แต่ไม่บันทึก argument ที่เป็นความลับ
- [ ] ผู้ใช้ทดสอบ end-to-end บนเครื่องจริง แล้วลงนามรับทราบ

## 4. ยังไม่ทำ
- ไม่เปลี่ยน `approvals.mode` ของ Hermes (คง `smart`), ไม่ใช้ YOLO
- ไม่แตะ routing/model, ไม่ commit/push, ไม่ restart Gateway
- ไม่เปิด LAN/tunnel/autostart

## 4b. ชั้น B (เขียนไฟล์/รันคำสั่ง) — **นอกขอบเขต v1**
ยังปิดถาวร และจะไม่ถูกเปิดด้วยกลไกเดิมที่ใช้กับชั้น A เพราะ:
- การอ่านไฟล์มีเพดานความเสี่ยงต่ำและย้อนกลับได้ ส่วนการเขียน/รันคำสั่งเปลี่ยน state จริงและอาจย้อนไม่ได้
- ถ้าจะทำจริงต้องมีอย่างน้อย: sandbox (tmp root จำลอง + canary ที่ต้องไม่เปลี่ยน), audit log ที่บันทึก action+digest+ผล (ไม่บันทึก argument ลับ), per-action approval ที่ผูก path/คำสั่งเต็มรูปแบบ + expiry, และเกณฑ์ “fixture ไม่เปลี่ยนจนกว่าจะอนุมัติ” ที่มี test จริง
- อนุมัติเสียง/คำว่า “ใช่” ทั่วไป **ไม่มีวัน** ใช้เป็นสิทธิ์ได้ — ต้องเป็นการกดปุ่มยืนยันบน UI ที่ผูกกับ action นั้น ๆ เท่านั้น

## 5. การย้อนกลับ
ลบ `docs` ที่เกี่ยวข้อง + ปิด endpoint ใหม่ + คืน `agent_worker.py` เป็น gates เดิม (git ไม่มี commit จึงกู้จากไฟล์สำรอง/ไฟล์ปัจจุบัน) — เกณฑ์: ถ้ามีข้อสงสัยใด ๆ เรื่อง integrity ให้เลือกปิดมากกว่าเปิด
