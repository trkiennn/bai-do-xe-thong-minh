SMART PARKING - KHONG CAN THU MUC WEB

1. Mo mo phong nguon truc tiep tu:
   https://trkiennn.github.io/sda/

2. Python mo Chrome hien thi mo phong nguon va mo mot cua so Tkinter lam giao dien CAM BIEN.
   Khong con Flask, web/index.html, web/app.js, web/style.css.

3. LOGIC VI TRI:
   - Co 15 cam bien doc lap P01..P15.
   - P01..P15 duoc mapping theo toa do vat ly tren man hinh: hang tren trai->phai,
     hang giua trai->phai, hang duoi trai->phai.
   - Khong dung tong so xe de suy ra vi tri.
   - Xe roi P05 thi P05 TRONG ngay, du xe van dang chay trong bai.
   - Xe vao va do hoan tat P05 thi P05 CO XE.
   - Cac vi tri khac khong thay doi.

4. START/STOP:
   - Mo phong nguon Dung: cam bien hien TAM NGUNG, giu trang thai cu.
   - Bam Bat dau/Tiep tuc: bat dau gui 15 bit sensor vao Verilog.
   - Bam Dung: dung cap nhat cam bien.

5. CAI DAT:
   python -m venv venv
   venv\\Scripts\\activate
   pip install -r requirements.txt
   python -m playwright install chromium

6. CAN Icarus Verilog:
   iverilog va vvp phai co trong PATH.

7. CHAY:
   python parking_main.py

8. Khi chay se co 2 cua so:
   - Chrome: mo phong nguon, ban tu bam Bat dau/Dung.
   - Smart Parking Sensor: hien 15 vi tri va thong bao xe vao/roi.

Luu y: phai co Internet de mo trang GitHub Pages nguon.
