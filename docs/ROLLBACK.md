# การเริ่ม หยุด และย้อนกลับ

รุ่นนี้เป็น **voice-only pilot** ไม่ใช่ agent automation ที่เสร็จสมบูรณ์ ไม่มีการเปิด LAN/tunnel/autostart และไม่เปลี่ยน Gateway, model routing หรือ credentials

## เริ่ม
```sh
cd /Volumes/Data_Sufayan/git_project/jarvis-hermes-dashboard
python3 start-thai.py --no-browser
```
เปิด http://127.0.0.1:8730 บนเครื่องนี้ หากพอร์ตถูกใช้งาน ให้เลือก `--port 8731` และใช้ URL พอร์ตนั้น ไม่ปิดบริการเดิมเพื่อแย่งพอร์ต

## หยุด
กด Ctrl+C ใน terminal ที่เปิดบริการ หยุดเสียงในหน้าเว็บก่อนปิดแท็บ ปิดแท็บไม่เท่ากับหยุดเซิร์ฟเวอร์ และ abort ฝั่งเว็บไม่รับรองว่ายกเลิกการประมวลผล provider ฝั่งเซิร์ฟเวอร์ทันที งานที่ค้างถูกจำกัด timeout และไฟล์ชั่วคราวถูกลบเมื่อจบหรือหมดเวลา

## ย้อนกลับ
หยุดเฉพาะ Dashboard แล้วกลับไปใช้ Hermes Desktop เดิม ไม่ต้องลบ checkout หรือ restore config ทั้งไฟล์ Dashboard ไม่แก้ค่า config เดิม (ค่า STT medium/th และ TTS Niwat ตั้งแยกในระยะก่อนหน้า) ไม่ลบ cache โมเดลหรือ backups อัตโนมัติ

## ขอบเขต
API run/jobs ปิดด้วย HTTP 503 ปุ่มส่ง Agent ถูกปิด การถอดเสียงต้องกดบันทึกและตรวจข้อความ การอ่านเสียงต้องกดอ่านอย่างชัดเจน ข้อความ TTS ส่งไป Edge ออนไลน์; STT ประมวลผลภายในเครื่องหลังโมเดลพร้อม ไม่มี fallback ไปบริการ STT คลาวด์
