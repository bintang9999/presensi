# Gunakan image Python slim untuk efisiensi ukuran
FROM python:3.12-slim

# Tentukan direktori kerja di dalam container
WORKDIR /app

# Salin requirements.txt terlebih dahulu agar bisa menggunakan caching layer Docker
COPY requirements.txt .

# Instal semua dependensi
RUN pip install --no-cache-dir -r requirements.txt

# Salin seluruh kode aplikasi ke dalam container
COPY . .

# Jalankan bot presensi
CMD ["python", "v5.py"]
