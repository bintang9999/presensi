import requests
from bs4 import BeautifulSoup
import time
import json
import os
import signal
import sys
import re
import random
import threading
import telebot
from telebot import types

# ==================== KONFIGURASI ====================
from dotenv import load_dotenv

load_dotenv()
# Railway akan mengambil nilai ini dari menu 'Variables' di dashboard
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID   = os.getenv("CHAT_ID")
F1_VALUE  = os.getenv("F1_VALUE")
F2_VALUE  = os.getenv("F2_VALUE")
ID_MHS    = os.getenv("ID_MHS", "10577") # Default ke ID kamu jika tidak diatur

STATE_FILE = "sudah_absen.json"
SETTINGS_FILE = "bot_settings.json"
BASE_URL   = "https://raising.almaata.ac.id"
# ===========================================================

session = requests.Session()
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Origin": BASE_URL
}

# Variable untuk menyimpan jalur (namespace) dinamis dari server
CURRENT_NAMESPACE = "" 
SUDAH_ABSEN = set()
bot = telebot.TeleBot(BOT_TOKEN)
monitor_event = threading.Event()

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return set(json.load(f))
        except Exception:
            pass
    return set()

def save_state(state):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(list(state), f)
    except Exception as e:
        print(f"Error saving state: {e}")

def load_settings():
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"is_monitoring": False}

def save_settings(settings):
    try:
        with open(SETTINGS_FILE, "w") as f:
            json.dump(settings, f, indent=4)
    except Exception as e:
        print(f"Error saving settings: {e}")

def send_telegram(msg):
    try:
        bot.send_message(CHAT_ID, msg, parse_mode="Markdown")
    except Exception as e:
        print(f"Error send_telegram: {e}")

def shutdown_handler(sig, frame):
    save_state(SUDAH_ABSEN)
    print("\n🛑 Shutdown aman. State disimpan.")
    sys.exit(0)

signal.signal(signal.SIGINT, shutdown_handler)

# ===================== CORE LOGIC =====================

def sync_session_and_namespace():
    global CURRENT_NAMESPACE
    
    url_form = f"{BASE_URL}/welcome" # URL tempat kita mengambil form & CSRF
    url_post = f"{BASE_URL}/auth/login" # Default tebakan endpoint pemroses login

    try:
        print("🔍 Sinkronisasi Namespace & Session...")
        
        # 1. GET halaman /welcome untuk mengambil Token
        r_get = session.get(url_form, headers=HEADERS, timeout=60)
        
        if r_get.status_code != 200:
            print(f"❌ Server Kampus Error {r_get.status_code}. Menunggu...")
            return False

        soup = BeautifulSoup(r_get.text, "html.parser")
        
        # Cari token CSRF
        csrf_input = soup.find("input", {"name": "csrf_test_name"})
        if csrf_input is None:
            token = session.cookies.get("csrf_cookie_name")
            if not token:
                print("❌ Gagal mendapatkan Token CSRF. Struktur HTML mungkin berubah.")
                return False
        else:
            token = csrf_input.get("value")

        # 2. INTEL: Cari tahu ke mana form sebenarnya di-submit (atribut action)
        login_form = soup.find("form")
        if login_form and login_form.get("action"):
            url_post = login_form.get("action")
            # Jika action-nya berupa path relatif (misal: "auth/login"), gabungkan dengan BASE_URL
            if not url_post.startswith("http"):
                url_post = f"{BASE_URL}/{url_post.lstrip('/')}"
                
        # 3. POST data login ke url_post yang sudah ditemukan
        payload = {'csrf_test_name': token, 'f1': F1_VALUE, 'f2': F2_VALUE, 'slogin': 'LOGIN'}
        # Perhatikan: Referer kita set ke url_form agar server mengira kita mengeklik dari halaman /welcome
        r_post = session.post(url_post, data=payload, headers={"Referer": url_form, **HEADERS}, allow_redirects=True)
        
        # Ekstraksi Namespace dari URL setelah redirect
        if session.cookies.get("ci_session") and "logout" in r_post.text.lower():
            match = re.search(r'almaata\.ac\.id/([a-f0-9]{32,40})/', r_post.url)
            if match:
                CURRENT_NAMESPACE = match.group(1)
                print(f"✅ Sesi Aktif. Namespace: {CURRENT_NAMESPACE[:8]}...")
                return True
            else:
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
        session.get(url_dashboard, headers=HEADERS, timeout=60)
        
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
        
        r = session.post(url_api, data=payload, headers=local_headers, timeout=60)
        
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

def monitoring_loop():
    global CURRENT_NAMESPACE, SUDAH_ABSEN
    print("🚀 Bot Monitoring Thread Aktif!")
    
    loop_count = 0
    while True:
        try:
            settings = load_settings()
            if not settings.get("is_monitoring", False):
                print(f"⏳ [{time.strftime('%H:%M:%S')}] Pemantauan dinonaktifkan. Menunggu instruksi dari Telegram...")
                # Menunggu event diset (jika dinonaktifkan, thread ini akan block di sini)
                monitor_event.wait()
                monitor_event.clear()
                continue
                
            if not CURRENT_NAMESPACE:
                if not sync_session_and_namespace():
                    print("❌ Gagal login. Mencoba lagi dalam 60 detik...")
                    monitor_event.wait(timeout=60)
                    continue

            # Rutin kirim laporan setiap ~1 jam (sekitar 20 kali loop jika sleep 3 menit)
            if loop_count >= 20:
                send_telegram("🛡️ *Laporan Rutin*: Bot masih standby memantau presensi.")
                loop_count = 0

            api_url = f"{BASE_URL}/{CURRENT_NAMESPACE}/api/datatable/perkuliahan/daftar_pertemuan_presensi_mahasiswa/{ID_MHS}"
            r = session.get(api_url, params={"length": 15}, headers=HEADERS, timeout=60)

            if r.status_code != 200 or "json" not in r.headers.get("Content-Type", ""):
                print("⚠️ Sesi habis atau server bermasalah, melakukan sinkronisasi ulang...")
                if sync_session_and_namespace():
                    continue
                else:
                    monitor_event.wait(timeout=60)
                    continue

            data = r.json().get("data", [])
            for m in data:
                idp = str(m["id_pertemuan_presensi"])
                matkul = m["nama_matakuliah"]
                kode = m["kode"]
                is_done = str(m.get("status_presensi", "0"))

                if kode and kode != "-" and is_done == "0" and idp not in SUDAH_ABSEN:
                    if tembak_presensi(idp, kode, matkul):
                        SUDAH_ABSEN.add(idp)
                        save_state(SUDAH_ABSEN)

            loop_count += 1
            sleep_time = random.randint(120, 180)
            print(f"⏳ Standby [{time.strftime('%H:%M:%S')}] Namespace: {CURRENT_NAMESPACE[:8]}...")
            monitor_event.wait(timeout=sleep_time)

        except Exception as e:
            print(f"\n⚠️ Error di loop pemantauan: {e}")
            monitor_event.wait(timeout=30)
            sync_session_and_namespace()


# ==================== TELEGRAM BOT LOGIC ====================

def get_main_menu():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    btn_start = types.KeyboardButton("🟢 Mulai Pantau")
    btn_stop = types.KeyboardButton("🔴 Hentikan Pantau")
    btn_status = types.KeyboardButton("📊 Status Bot")
    markup.row(btn_start, btn_stop)
    markup.row(btn_status)
    return markup

def is_authorized(message):
    # Bandingkan sebagai string untuk menghindari tipe data yang tidak cocok
    return str(message.chat.id) == str(CHAT_ID)

@bot.message_handler(func=lambda msg: not is_authorized(msg))
def unauthorized(message):
    bot.reply_to(message, "⛔ Maaf, Anda tidak memiliki izin untuk mengontrol bot ini.")

@bot.message_handler(commands=['start', 'menu'])
def send_welcome(message):
    settings = load_settings()
    is_mon = settings.get("is_monitoring", False)
    status_text = "🟢 Aktif" if is_mon else "🔴 Nonaktif"
    
    welcome_msg = (
        "👋 *Halo! Selamat datang di Bot Controller Presensi.*\n\n"
        f"Status Pemantauan Saat Ini: *{status_text}*\n\n"
        "Gunakan tombol di bawah untuk mengontrol bot:"
    )
    bot.send_message(message.chat.id, welcome_msg, parse_mode="Markdown", reply_markup=get_main_menu())

@bot.message_handler(func=lambda msg: msg.text == "🟢 Mulai Pantau")
def handle_start_monitoring(message):
    settings = load_settings()
    settings["is_monitoring"] = True
    save_settings(settings)
    
    monitor_event.set()
    
    bot.send_message(
        message.chat.id,
        "🟢 *Pemantauan Presensi Diaktifkan!*\n"
        "Bot sekarang memantau presensi Anda setiap 2-3 menit di latar belakang secara otomatis.",
        parse_mode="Markdown",
        reply_markup=get_main_menu()
    )

@bot.message_handler(func=lambda msg: msg.text == "🔴 Hentikan Pantau")
def handle_stop_monitoring(message):
    settings = load_settings()
    settings["is_monitoring"] = False
    save_settings(settings)
    
    bot.send_message(
        message.chat.id,
        "🔴 *Pemantauan Presensi Dihentikan!*\n"
        "Bot tidak akan mengecek ke server kampus, tetapi bot tetap standby menerima perintah.",
        parse_mode="Markdown",
        reply_markup=get_main_menu()
    )

@bot.message_handler(func=lambda msg: msg.text == "📊 Status Bot")
def handle_status(message):
    settings = load_settings()
    is_mon = settings.get("is_monitoring", False)
    status_text = "🟢 Aktif (Memantau)" if is_mon else "🔴 Nonaktif (Pause)"
    
    session_text = "Terhubung ✅" if CURRENT_NAMESPACE else "Belum Login / Sesi Kedaluwarsa ❌"
    
    total_absen = len(SUDAH_ABSEN)
    
    status_msg = (
        "📊 *STATUS BOT PRESENSI*\n\n"
        f"• *Auto-Monitoring:* {status_text}\n"
        f"• *Sesi Kampus:* {session_text}\n"
        f"• *Namespace:* `{CURRENT_NAMESPACE[:8] if CURRENT_NAMESPACE else '-'}`\n"
        f"• *Total Absen Tersimpan:* `{total_absen}` kelas\n"
        f"• *Mahasiswa ID:* `{ID_MHS}`"
    )
    bot.send_message(message.chat.id, status_msg, parse_mode="Markdown", reply_markup=get_main_menu())

# ===================== RUN ===========================

if __name__ == "__main__":
    # Load state awal
    SUDAH_ABSEN = load_state()
    
    # Ambil pengaturan awal
    settings = load_settings()
    
    # Jalankan background thread
    t = threading.Thread(target=monitoring_loop, daemon=True)
    t.start()
    
    # Jika di setting bernilai true, aktifkan pemantauan
    if settings.get("is_monitoring", False):
        monitor_event.set()
        
    print("🚀 Bot Controller Aktif!")
    try:
        send_telegram("🚀 *Bot Presensi Telah Dinyalakan!*\nStatus: Standby.\nKirim /menu atau gunakan tombol di bawah untuk interaksi.")
    except Exception as e:
        print(f"Gagal mengirim pesan startup: {e}")
        
    # Jalankan polling Telegram
    bot.infinity_polling()
