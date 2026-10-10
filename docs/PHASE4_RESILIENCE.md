# Phase 4 — ความทนทานและความปลอดภัย (ผลตรวจสอบจริง)

ไฟล์ทดสอบ: `tests/test_phase4_resilience.py` (8 tests) — รันบน port ephemeral ของตัวเอง ไม่แตะ server pilot ที่ 8730 ไม่เรียก LLM จริง ไม่มีไมค์/การเล่นเสียง

## ครอบคลุม

| # | กรณี | หลักฐาน |
|---|---|---|
| 1 | restart เสีย state ชั่วคราว + fail-closed | bridge ใหม่ไม่มี history เดิม; ถ้า readiness proof ล้ม → `/api/run` 503 ทันที |
| 2 | contention สองแท็บ | run ที่สองระหว่างงานแรก → **409** (ไม่เข้าคิวลับ) ; คำตอบของแท็บ A ไม่ไปอยู่กับแท็บ B ; lock คืน; replay id ที่ *รับไปแล้ว* → 409 |
| 3 | guard matrix 14 กรณี | wrong/missing token 401 · wrong Origin 403 · wrong Host 403 · wrong MIME 415 · oversize 413 · empty/bad JSON/not-object 400 · unconfirmed/slash/bad-UUID 400 · unknown endpoint 404 · `/api/jobs` 503 · chunked framing 400 → **backend ถูกเรียก 0 ครั้ง** (นับด้วย counter) |
| 4 | ไม่รั่ว secret | response body ทุกกรณีไม่มี token / `sk-` / `Bearer` |
| 5 | TTS timeout | คืน 503 + ข้อความ error ชัดเจน ไม่คืน audio ปลอม |
| 6 | STT busy | คืน 503 + `text:""` + error ไม่คืน transcript ปลอม |
| 7 | audio ว่าง/เกินขนาด | 400/413 ก่อนถึง backend (counter = 0) |
| 8 | temp + store ไม่ถูกแตะ | readiness proof ผ่าน managed runtime จริงแล้ว scratch ไม่มีไฟล์ค้าง; SHA-256 ของ `~/.hermes/config.yaml` และ `auth.json` เท่าเดิมทุก test (autouse fixture) |

## ข้อค้นพบที่ต้องรู้

1. **Hermes กัน test แตะ auth store จริง** — ถ้า managed child สืบทอด marker `PYTEST_CURRENT_TEST` จะถูก `hermes_cli/auth.py` ปฏิเสธ (`Refusing to touch real user auth store during test run`) ทดสอบจึงลบ marker นั้นให้ child ตัวเดียว และพิสูจน์ซ้ำว่า auth/config ไม่ถูกแก้ (SHA-256 ก่อน/หลัง) — เหมือนพฤติกรรมตอนเปิด server จริง
2. **คำขอที่ถูกปฏิเสธเพราะ “busy” ไม่ถูกจอง request id** จึง retry ได้ตามปกติ ส่วน id ที่รับไปแล้วกัน replay 409 — 的行为 ถูกต้อง แต่เดิมเข้าใจผิดว่าทุก id ติด replay
3. ยังคง fail-closed: ไม่มี universal approval → ไม่เปิดเครื่องมือ/`/api/jobs`

## ยังทดสอบอัตโนมัติไม่ได้ (ต้องให้ผู้ใช้ทำ)

- การกดไมค์จริง + TCC permission + สลับอุปกรณ์เสียง (ต้องลองบนเครื่องจริง)
- การเล่นเสียง/ปุ่มหยุด ด้วยเบราว์เซอร์ของผู้ใช้ (headless ไม่นับ)
- device switch, network drop จริง, provider rate-limit จริง (จำลองด้วย fixture แทน)

## รัน

```sh
cd /Volumes/Data_Sufayan/git_project/jarvis-hermes-dashboard
python3 -m pytest -q tests/        # ปัจจุบัน 33 passed
node --test tests/ui/*.test.js ui/*.test.js   # 21 passed
```
