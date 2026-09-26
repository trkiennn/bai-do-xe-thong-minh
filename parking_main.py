import re
import subprocess
import threading
import time
import queue
from pathlib import Path
import tkinter as tk
from tkinter import ttk

from playwright.sync_api import sync_playwright

BASE_DIR = Path(__file__).resolve().parent
SOURCE_URL = "https://trkiennn.github.io/sda/"
TOTAL_SLOTS = 15
POLL_INTERVAL = 0.5

state_lock = threading.Lock()
state = {
    "connected": False,
    "source_running": False,
    "occupied": 0,
    "empty": TOTAL_SLOTS,
    "full": False,
    "allow_entry": True,
    "cars_in": 0,
    "cars_out": 0,
    "max_occupied": 0,
    "slots": [False] * TOTAL_SLOTS,
    "message": "Đang mở mô phỏng nguồn...",
    "last_event": "Chưa có sự kiện",
    "last_event_time": "--",
    "source_error": "",
    "updated_at": "--",
}
ui_queue = queue.Queue()


def get_state():
    with state_lock:
        return {**state, "slots": list(state["slots"])}


def set_state(**kwargs):
    with state_lock:
        state.update(kwargs)


def notify_ui():
    ui_queue.put(1)


def number_after_label(body_text, label):
    m = re.search(re.escape(label) + r"\s*:?\s*(\d+)", body_text, re.I)
    return int(m.group(1)) if m else None


def ensure_space_overlay_visible(page):
    """Turn on the source's own space-status overlay when possible.

    This does not change the simulation; it only makes #spaces expose the
    source's red/yellow/purple/white state visually and in computed styles.
    """
    try:
        controls = page.locator("#show-spaces-button, input[value*='Show Spaces'], input[value*='Hiển thị']")
        if controls.count() > 0:
            el = controls.nth(0)
            if el.is_visible():
                # The source button is a toggle. Click once only if overlay is
                # currently hidden. The class is the source's own indicator.
                shown = page.locator("#spaces.show-overlay-children").count() > 0
                if not shown:
                    el.click(timeout=500)
    except Exception:
        pass


def extract_slot_states(page):
    """Read each physical parking slot independently.

    Mapping is purely geometric: top-to-bottom rows, left-to-right inside each
    row. It never uses the number of cars to assign a slot.

    Occupancy rule:
      - red space => occupied
      - stationary car substantially overlapping the space => occupied
      - yellow => reserved/entering => empty until parking finishes
      - purple + stationary car in the space => occupied
      - purple + moving/animation => empty (the car is leaving/entering)
      - no car / white/gray => empty
    """
    script = r"""
    (total) => {
      const root = document.querySelector('#spaces');
      const lot = document.querySelector('#parking-lot');
      if (!root || !lot) return {ok:false, reason:'missing #spaces or #parking-lot'};

      const visible = el => {
        if (!el) return false;
        const r = el.getBoundingClientRect();
        const cs = getComputedStyle(el);
        return r.width > 3 && r.height > 3 &&
          cs.display !== 'none' && cs.visibility !== 'hidden' &&
          parseFloat(cs.opacity || '1') > 0;
      };
      const overlap = (a,b) => {
        const l=Math.max(a.left,b.left), r=Math.min(a.right,b.right);
        const t=Math.max(a.top,b.top), bot=Math.min(a.bottom,b.bottom);
        if(r<=l || bot<=t) return 0;
        return (r-l)*(bot-t);
      };

      let spaces = Array.from(root.querySelectorAll(':scope > .space'))
        .filter(visible)
        .map((el,i)=>({el,i,rect:el.getBoundingClientRect()}));
      if (spaces.length !== total) {
        return {ok:false, reason:`expected ${total} visible spaces, got ${spaces.length}`};
      }

      // The simulation creates .car-wrapper > .car > img.
      // Do not depend on color or DOM order for the car identity.
      const cars = Array.from(lot.querySelectorAll('.car-wrapper'))
        .map(wrapper => {
          const car = wrapper.querySelector('.car');
          if (!car || !visible(car)) return null;
          const rect = car.getBoundingClientRect();
          const cs = getComputedStyle(car);
          let running = false;
          if (typeof car.getAnimations === 'function') {
            running = car.getAnimations().some(a => a.playState === 'running');
          }
          if (cs.animationName && cs.animationName !== 'none' && cs.animationDuration !== '0s') {
            running = running || cs.animationPlayState === 'running';
          }
          return {wrapper, car, rect, running};
        }).filter(Boolean);

      const parseRGB = value => {
        const m=(value||'').match(/rgba?\\(([^)]+)\\)/i);
        if(!m) return null;
        const a=m[1].split(',').map(v=>parseFloat(v.trim()));
        return a.length>=3 && a.slice(0,3).every(Number.isFinite) ? a : null;
      };
      const colorKind = el => {
        const vals=[getComputedStyle(el).backgroundColor, el.style.backgroundColor, getComputedStyle(el).borderColor];
        for(const value of vals){
          const rgb=parseRGB(value);
          if(!rgb) continue;
          const [r,g,b]=rgb;
          if(r>=170 && g<120 && b<120) return 'red';
          if(r>=170 && g>=120 && b<130) return 'yellow';
          if(b>=110 && b>r*1.15 && b>g*1.15) return 'purple';
        }
        return 'other';
      };

      // Stable physical order. This is deliberately independent of space id
      // because the source ranks spaces by desirability, not by screen order.
      spaces.sort((a,b)=>{
        const ay=(a.rect.top+a.rect.bottom)/2, by=(b.rect.top+b.rect.bottom)/2;
        const tolerance=Math.max(18, Math.min(a.rect.height,b.rect.height)*0.35);
        if(Math.abs(ay-by)>tolerance) return ay-by;
        return a.rect.left-b.rect.left;
      });

      // Re-group after the first sort to make row handling stable on resized windows.
      const rows=[];
      for(const s of spaces){
        const cy=(s.rect.top+s.rect.bottom)/2;
        let row=rows.find(r=>Math.abs(cy-r.cy)<=Math.max(18,s.rect.height*0.35));
        if(!row){row={cy,items:[]};rows.push(row);}
        row.items.push(s);
        row.cy=row.items.reduce((sum,x)=>sum+(x.rect.top+x.rect.bottom)/2,0)/row.items.length;
      }
      rows.sort((a,b)=>a.cy-b.cy);
      spaces=rows.flatMap(r=>r.items.sort((a,b)=>a.rect.left-b.rect.left));
      if(spaces.length!==total) return {ok:false, reason:'mapping did not produce 15 spaces'};

      const states=spaces.map(space=>{
        const area=space.rect.width*space.rect.height;
        const matches=cars.map(car=>{
          const ov=overlap(space.rect,car.rect);
          return {
            car,
            spaceRatio:ov/area,
            carRatio:ov/Math.max(1,car.rect.width*car.rect.height)
          };
        }).sort((a,b)=>Math.max(b.spaceRatio,b.carRatio)-Math.max(a.spaceRatio,a.carRatio));
        const nearest=matches[0] || null;
        const kind=colorKind(space.el);

        if(kind==='red') return true;
        if(kind==='yellow') return false;

        if(nearest && nearest.spaceRatio>=0.50){
          // A moving car is not considered parked. This is what makes the
          // exact slot turn empty as soon as the car leaves that slot.
          if(nearest.car.running) return false;
          return true;
        }

        // Purple means parked/finished OR leaving in the source. If a
        // stationary car still occupies most of the slot, it is parked;
        // otherwise it has left the slot and the sensor must be empty.
        if(kind==='purple'){
          return !!(nearest && !nearest.car.running && nearest.spaceRatio>=0.50);
        }
        return !!(nearest && !nearest.car.running && nearest.spaceRatio>=0.70);
      });

      return {ok:true, states, colors:spaces.map(s=>colorKind(s.el)), cars:cars.length};
    }
    """
    try:
        result = page.evaluate(script, TOTAL_SLOTS)
        if isinstance(result, dict) and result.get("ok"):
            states = result.get("states")
            if isinstance(states,list) and len(states)==TOTAL_SLOTS and all(isinstance(x,bool) for x in states):
                return states, ""
            return None, "Trạng thái 15 vị trí không hợp lệ."
        reason = result.get("reason", "không rõ lỗi") if isinstance(result,dict) else "không rõ lỗi"
        return None, reason
    except Exception as e:
        return None, str(e)


def read_source(page):
    ensure_space_overlay_visible(page)
    body = page.locator("body").inner_text()
    entered = number_after_label(body, "Cars which have entered the lot")
    parked = number_after_label(body, "Cars currently parked")
    left = number_after_label(body, "Cars which have left the lot")
    if entered is None or parked is None or left is None:
        raise RuntimeError("Không đọc được bộ đếm của mô phỏng nguồn.")

    slots, reason = extract_slot_states(page)
    if slots is None:
        raise RuntimeError("Không đọc được 15 vị trí: " + reason)

    return {"cars_in":entered, "occupied":sum(slots), "cars_out":left, "slots":slots}


def detect_source_running(page):
    """Read the actual Start/Stop control of the user's source simulation."""
    try:
        # The fork used by the project has a single toggle whose label changes.
        candidates = page.locator("button, input[type='button'], input[type='submit'], [role='button']")
        visible_text=[]
        for i in range(min(candidates.count(),100)):
            el=candidates.nth(i)
            try:
                if not el.is_visible():
                    continue
                vals=[el.inner_text(timeout=100) or "", el.get_attribute('value') or "",
                      el.get_attribute('aria-label') or "", el.get_attribute('title') or ""]
                txt=" ".join(v for v in vals if v).strip().lower()
                if txt: visible_text.append(txt)
            except Exception:
                pass

        # Exact/strong phrases first. Only one toggle should be active at once.
        for txt in visible_text:
            if any(x in txt for x in ("tiếp tục mô phỏng", "tiep tuc mo phong", "resume simulation", "resume")):
                return False
        for txt in visible_text:
            if any(x in txt for x in ("dừng mô phỏng", "dung mo phong", "stop simulation")):
                return True

        # Some versions expose a JS loop object. Read it without depending on it.
        result=page.evaluate("""() => {
          try {
            if (window.loop && typeof window.loop.running === 'boolean') return window.loop.running;
          } catch(e) {}
          return null;
        }""")
        if isinstance(result,bool): return result
    except Exception:
        pass
    # If no explicit pause control is exposed, the source is continuously running.
    return True


def sensor_to_bits(sensor):
    return ''.join('1' if x else '0' for x in sensor)


def compile_verilog():
    exe=BASE_DIR/'parking_sim'
    r=subprocess.run(['iverilog','-o',str(exe),'parking_system.v','parking_tb.v'],cwd=BASE_DIR,capture_output=True,text=True)
    if r.returncode!=0: raise RuntimeError('Không compile được Verilog:\n'+r.stderr)


def send_to_verilog(sensor):
    (BASE_DIR/'parking_input.txt').write_text(sensor_to_bits(sensor)+'\n',encoding='utf-8')
    r=subprocess.run(['vvp',str(BASE_DIR/'parking_sim')],cwd=BASE_DIR,capture_output=True,text=True)
    if r.returncode!=0: raise RuntimeError('Lỗi Verilog:\n'+r.stderr)
    for line in r.stdout.splitlines():
        p=line.split()
        if len(p)==5 and p[0]=='RESULT':
            return {'occupied':int(p[1]),'empty':int(p[2]),'full':bool(int(p[3])),'allow_entry':bool(int(p[4]))}
    raise RuntimeError('Không tìm thấy RESULT từ Verilog.')


def update_from_data(data, previous_data, max_occupied):
    sensor=list(data['slots'])
    result=send_to_verilog(sensor)
    now=time.strftime('%H:%M:%S')
    event=None
    if previous_data:
        changes=[i for i in range(TOTAL_SLOTS) if previous_data['slots'][i]!=sensor[i]]
        if changes:
            event=' | '.join((f'Xe vào P{i+1:02d}' if sensor[i] else f'P{i+1:02d} TRỐNG - xe đã rời vị trí') for i in changes)
    max_occupied=max(max_occupied,result['occupied'])
    set_state(connected=True,source_running=True,occupied=result['occupied'],empty=result['empty'],
              full=result['full'],allow_entry=result['allow_entry'],cars_in=data['cars_in'],cars_out=data['cars_out'],
              max_occupied=max_occupied,slots=sensor,message=('BÃI XE ĐÃ ĐẦY' if result['full'] else f"CÒN {result['empty']} CHỖ TRỐNG"),
              source_error='',updated_at=now)
    if event: set_state(last_event=event,last_event_time=now)
    notify_ui()
    return max_occupied


def set_paused(last_data,max_occupied):
    slots=list(last_data['slots']) if last_data else [False]*TOTAL_SLOTS
    occ=sum(slots)
    set_state(connected=True,source_running=False,occupied=occ,empty=TOTAL_SLOTS-occ,full=occ>=TOTAL_SLOTS,
              allow_entry=occ<TOTAL_SLOTS,cars_in=last_data['cars_in'] if last_data else 0,
              cars_out=last_data['cars_out'] if last_data else 0,max_occupied=max_occupied,slots=slots,
              message='CẢM BIẾN TẠM NGỪNG - mô phỏng nguồn đang Dừng',source_error='',updated_at=time.strftime('%H:%M:%S'))
    notify_ui()


def monitor_source():
    previous_data=None
    max_occupied=0
    while True:
        try:
            set_state(connected=False,source_running=False,message='Đang mở mô phỏng nguồn...',source_error='')
            notify_ui()
            with sync_playwright() as p:
                browser=p.chromium.launch(headless=False)
                page=browser.new_page(viewport={'width':1200,'height':900})
                page.goto(SOURCE_URL,wait_until='domcontentloaded',timeout=30000)
                page.wait_for_timeout(1500)
                compile_verilog()
                # Retry DOM initialization without closing the browser.
                ready=False
                for _ in range(20):
                    try:
                        data=read_source(page)
                        ready=True
                        break
                    except Exception as e:
                        set_state(connected=True,source_running=False,message='Đang khởi tạo cảm biến...',source_error=str(e))
                        notify_ui(); page.wait_for_timeout(500)
                if not ready:
                    raise RuntimeError('Không thể khởi tạo 15 vị trí từ mô phỏng nguồn.')

                previous_data=data
                set_state(connected=True,source_running=False,message='Mô phỏng đã mở. Nhấn Bắt đầu để cảm biến hoạt động.',source_error='')
                notify_ui()

                while not page.is_closed():
                    try:
                        data=read_source(page)
                        running=detect_source_running(page)
                        if running:
                            max_occupied=update_from_data(data,previous_data,max_occupied)
                        else:
                            set_paused(previous_data,max_occupied)
                        # Always keep latest physical snapshot. This makes the
                        # next running cycle report exactly the slot that changed.
                        previous_data=data
                        page.wait_for_timeout(int(POLL_INTERVAL*1000))
                    except Exception as e:
                        set_state(connected=True,source_running=False,message='Đang chờ mô phỏng nguồn ổn định...',source_error=str(e),updated_at=time.strftime('%H:%M:%S'))
                        notify_ui()
                        page.wait_for_timeout(700)
                raise RuntimeError('Cửa sổ mô phỏng nguồn đã đóng.')
        except Exception as e:
            set_state(connected=False,source_running=False,message='Mất kết nối mô phỏng - sẽ mở lại sau 3 giây.',source_error=str(e),updated_at=time.strftime('%H:%M:%S'))
            notify_ui(); time.sleep(3)


class SensorUI:
    def __init__(self,root):
        self.root=root; root.title('Smart Parking Sensor - 15 vị trí'); root.geometry('1000x720'); root.minsize(850,620); root.configure(bg='#0b1220')
        style=ttk.Style();
        try: style.theme_use('clam')
        except Exception: pass
        title=tk.Label(root,text='SMART PARKING • CẢM BIẾN 15 VỊ TRÍ',font=('Segoe UI',20,'bold'),bg='#0b1220',fg='#f8fafc'); title.pack(pady=(18,5))
        self.status=tk.Label(root,text='ĐANG KẾT NỐI...',font=('Segoe UI',11,'bold'),bg='#334155',fg='white',padx=14,pady=8); self.status.pack(pady=(0,12))
        summary=tk.Frame(root,bg='#111827'); summary.pack(fill='x',padx=22,pady=5); self.vars={}
        for key,label in [('occupied','XE ĐANG TRONG BÃI'),('empty','CHỖ TRỐNG'),('cars_in','TỔNG XE VÀO'),('cars_out','TỔNG XE RA'),('max_occupied','CAO NHẤT')]:
            f=tk.Frame(summary,bg='#111827'); f.pack(side='left',expand=True,fill='both',padx=5,pady=12)
            tk.Label(f,text=label,bg='#111827',fg='#94a3b8',font=('Segoe UI',9,'bold')).pack()
            v=tk.Label(f,text='0',bg='#111827',fg='#f8fafc',font=('Segoe UI',22,'bold')); v.pack(); self.vars[key]=v
        self.message=tk.Label(root,text='',bg='#0b1220',fg='#67e8a5',font=('Segoe UI',12,'bold')); self.message.pack(pady=12)
        self.error=tk.Label(root,text='',bg='#0b1220',fg='#fca5a5',font=('Segoe UI',9)); self.error.pack()
        slots_frame=tk.Frame(root,bg='#0b1220'); slots_frame.pack(fill='both',expand=True,padx=22,pady=5); self.slot_labels=[]
        for i in range(TOTAL_SLOTS):
            r,c=divmod(i,5); card=tk.Frame(slots_frame,bg='#0d3022',bd=1,relief='solid'); card.grid(row=r,column=c,padx=6,pady=6,sticky='nsew'); slots_frame.grid_columnconfigure(c,weight=1); slots_frame.grid_rowconfigure(r,weight=1)
            name=tk.Label(card,text=f'P{i+1:02d}',bg='#0d3022',fg='#67e8a5',font=('Segoe UI',15,'bold')); name.pack(pady=(18,4))
            st=tk.Label(card,text='TRỐNG',bg='#0d3022',fg='#67e8a5',font=('Segoe UI',10,'bold')); st.pack(pady=(0,18)); self.slot_labels.append((card,name,st))
        event_frame=tk.Frame(root,bg='#111827'); event_frame.pack(fill='x',padx=22,pady=(8,18))
        self.event=tk.Label(event_frame,text='Sự kiện: Chưa có sự kiện',bg='#111827',fg='#f8fafc',anchor='w',font=('Segoe UI',11,'bold'),padx=15,pady=12); self.event.pack(fill='x')
        self.time_label=tk.Label(event_frame,text='Cập nhật: --',bg='#111827',fg='#94a3b8',anchor='w',font=('Segoe UI',9),padx=15,pady=10); self.time_label.pack(fill='x')
        root.protocol('WM_DELETE_WINDOW',self.close); self.refresh()
    def refresh(self):
        d=get_state(); self.status.config(text='● MẤT KẾT NỐI' if not d['connected'] else ('● TẠM NGỪNG' if not d['source_running'] else '● ĐANG CHẠY'),bg='#7f1d1d' if not d['connected'] else ('#92400e' if not d['source_running'] else '#166534'))
        for k,v in self.vars.items(): v.config(text=str(d[k]))
        self.message.config(text=d['message'],fg='#fb7185' if d['full'] else '#67e8a5'); self.error.config(text=d['source_error'][:180])
        self.event.config(text=f"Sự kiện: {d['last_event']}"); self.time_label.config(text=f"Cập nhật: {d['updated_at']}   |   {d['last_event_time']}")
        for i,occ in enumerate(d['slots']):
            card,name,st=self.slot_labels[i]; bg,fg,text=(('#351923','#fb7185','CÓ XE') if occ else ('#0d3022','#67e8a5','TRỐNG')); card.config(bg=bg); name.config(bg=bg,fg=fg); st.config(bg=bg,fg=fg,text=text)
        self.root.after(200,self.refresh)
    def close(self): self.root.destroy()


def main():
    threading.Thread(target=monitor_source,daemon=True).start()
    root=tk.Tk(); SensorUI(root); root.mainloop()

if __name__=='__main__': main()
