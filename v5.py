import requests
from bs4 import BeautifulSoup
import time
import json
import os
import signal
import sys
import re
import random

# ==================== KONFIGURASI ====================
import os

# ==================== KONFIGURASI CLOUD ====================
# Railway akan mengambil nilai ini dari menu 'Variables' di dashboard
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID   = os.getenv("CHAT_ID")
F1_VALUE  = os.getenv("F1_VALUE")
F2_VALUE  = os.getenv("F2_VALUE")
ID_MHS    = os.getenv("ID_MHS", "10577") # Default ke ID kamu jika tidak diatur

STATE_FILE = "sudah_absen.json"
BASE_URL   = "https://raising.almaata.ac.id"
# ===========================================================
# =====================================================

session = requests.Session()
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Origin": BASE_URL
}

# Variable untuk menyimpan jalur (namespace) dinamis dari server
CURRENT_NAMESPACE = "" 

def send_telegram(msg):
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=10)
    except: pass

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f: return set(json.load(f))
    return set()

def save_state(state):
    with open(STATE_FILE, "w") as f: json.dump(list(state), f)

def shutdown_handler(sig, frame):
    save_state(SUDAH_ABSEN)
    print("\n🛑 Shutdown aman. State disimpan.")
    sys.exit(0)

signal.signal(signal.SIGINT, shutdown_handler)

# ===================== CORE LOGIC =====================

def sync_session_and_namespace():
    global CURRENT_NAMESPACE
    url_login = f"{BASE_URL}/auth/login"
    try:
        print("🔍 Sinkronisasi Namespace & Session...")
        r_get = session.get(url_login, headers=HEADERS, timeout=60)
        
        # Validasi respon server
        if r_get.status_code != 200:
            print(f"❌ Server Kampus Error {r_get.status_code}. Menunggu...")
            return False

        soup = BeautifulSoup(r_get.text, "html.parser")
        
        # Pencarian Token CSRF secara aman
        csrf_input = soup.find("input", {"name": "csrf_test_name"})
        
        if csrf_input is None:
            # Fallback: Cek apakah token ada di cookie secara langsung
            token = session.cookies.get("csrf_cookie_name")
            if not token:
                print("❌ Gagal mendapatkan Token CSRF. Struktur HTML mungkin berubah.")
                return False
        else:
            token = csrf_input.get("value")

        # Kirim Login
        payload = {'csrf_test_name': token, 'f1': F1_VALUE, 'f2': F2_VALUE, 'slogin': 'LOGIN'}
        r_post = session.post(url_login, data=payload, headers={"Referer": url_login, **HEADERS}, allow_redirects=True)
        
        # Ekstraksi Namespace dari URL setelah redirect
        if session.cookies.get("ci_session") and "logout" in r_post.text.lower():
            match = re.search(r'almaata\.ac\.id/([a-f0-9]{32,40})/', r_post.url)
            if match:
                CURRENT_NAMESPACE = match.group(1)
                print(f"✅ Sesi Aktif. Namespace: {CURRENT_NAMESPACE[:8]}...")
                return True
            else:
                # Jika login sukses tapi URL tidak mengandung Hash panjang
                print("⚠️ Login sukses tapi Namespace dinamis tidak ditemukan di URL.")
                return False
        
        print("❌ Login ditolak. Periksa F1/F2 atau koneksi.")
        return False
    except Exception as e:
        print(f"❌ Error Auth Detail: {e}")
        return False

def tembak_presensi(idp, kode, matkul):
    """Mengikuti CSRF Lifecycle: Request halaman form -> Ambil Token Baru -> POST"""
    try:
        # 1. Kunjungi halaman dashboard untuk memicu rotasi CSRF token
        url_dashboard = f"{BASE_URL}/{CURRENT_NAMESPACE}/dashboard/perkuliahan/presensi"
        session.get(url_dashboard, headers=HEADERS, timeout=15)
        
        # 2. Ambil token terbaru dari cookie (CodeIgniter biasanya update ini otomatis)
        token_fresh = session.cookies.get("csrf_cookie_name")
        
        # 3. Eksekusi POST ke API dengan Namespace yang benar
        url_api = f"{BASE_URL}/{CURRENT_NAMESPACE}/api/perkuliahan/create_presensi_mahasiswa_by_kode/{idp}"
        payload = {
            "id_mahasiswa": ID_MHS,
            "kode_presensi": kode,
            "csrf_test_name": token_fresh
        }
        
        # Update Referer agar identik dengan aktivitas manusia
        local_headers = {**HEADERS, "Referer": url_dashboard}
        
        r = session.post(url_api, data=payload, headers=local_headers, timeout=15)
        
        if "json" in r.headers.get("Content-Type", ""):
            js = r.json()
            msg = js.get("message", "No Message")
            if js.get("status") or "berhasil" in msg.lower():
                send_telegram(f"✅ *AUTO PRESENSI BERHASIL*\n📚 {matkul}\n🔑 Kode: `{kode}`")
                return True
            send_telegram(f"⚠️ *DITOLAK SERVER*\n📚 {matkul}\n💬 {msg}")
        return False
    except Exception as e:
        send_telegram(f"❌ *SYSTEM ERROR*\n{matkul}\n`{e}`")
        return False

def monitoring():
    print("🚀 Bot v5.3 (Health Check Active) Aktif!")
    # Kirim sinyal hidup saat pertama kali start
    send_telegram("🚀 *Bot v5.3 Aktif*\nStatus: Memantau 24/7")
    
    if not sync_session_and_namespace():
        return

    sudah_absen = load_state()
    loop_count = 0

    while True:
        try:
            # Setiap 20 kali pengecekan (sekitar 1 jam), kirim kabar ke Telegram
            # Agar kamu tahu bot masih hidup tanpa perlu nanya
            if loop_count >= 20:
                send_telegram("🛡️ *Laporan Rutin*: Bot masih standby memantau presensi.")
                loop_count = 0
            
            api_url = f"{BASE_URL}/{CURRENT_NAMESPACE}/api/datatable/perkuliahan/daftar_pertemuan_presensi_mahasiswa/{ID_MHS}"
            r = session.get(api_url, params={"length": 15}, headers=HEADERS, timeout=15)

            if r.status_code != 200 or "json" not in r.headers.get("Content-Type", ""):
                if sync_session_and_namespace():
                    continue 
                else:
                    time.sleep(60)
                    continue

            data = r.json().get("data", [])
            for m in data:
                idp = str(m["id_pertemuan_presensi"])
                matkul = m["nama_matakuliah"]
                kode = m["kode"]
                is_done = str(m.get("status_presensi", "0"))

                if kode and kode != "-" and is_done == "0" and idp not in sudah_absen:
                    if tembak_presensi(idp, kode, matkul):
                        sudah_absen.add(idp)
                        save_state(sudah_absen)

            loop_count += 1
            sleep_time = random.randint(120, 180)
            print(f"⏳ Standby [{time.strftime('%H:%M:%S')}] Namespace: {CURRENT_NAMESPACE[:8]}", end="\r")
            time.sleep(sleep_time)

        except Exception as e:
            print(f"\n⚠️ Error: {e}")
            time.sleep(30)
            sync_session_and_namespace()

# ===================== RUN ===========================
SUDAH_ABSEN = load_state()

if __name__ == "__main__":

    monitoring()

