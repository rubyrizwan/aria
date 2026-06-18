# API Checker

Dashboard ringan untuk mendeteksi endpoint yang kompatibel dengan OpenAI atau Anthropic,
memeriksa API key, dan mengambil daftar model secara berkala. Aplikasi berjalan sebagai
satu proses FastAPI, menyimpan data di SQLite, dan mengenkripsi API key sebelum
menyimpannya.

Versi saat ini: **0.4.0**. Fitur token usage belum termasuk dalam versi ini.

## Fitur

- Deteksi otomatis OpenAI-compatible dan Anthropic-compatible
- Penemuan model melalui `/v1/models` atau `/models`
- API key opsional untuk endpoint publik
- Interval per provider: 1, 5, 15, 30, atau 60 menit
- Dashboard status, compatibility, daftar model, latency, dan histori pemeriksaan
- Pencarian dan pagination model, maksimal 30 baris per halaman
- Indikator capability model dari metadata provider atau inferensi nama model
- Toggle monitoring per provider dan monitoring otomatis global
- Pemeriksaan manual menggunakan tombol `Check now`
- Halaman Settings dan About
- API key terenkripsi dengan Fernet
- Retensi histori otomatis, default 30 hari
- Pengaturan global untuk mengaktifkan atau menonaktifkan monitoring otomatis
- UI responsif tanpa dependency frontend dari CDN
- Server entrypoint selalu bind ke `127.0.0.1`

## Instalasi

Prasyarat: Python 3.12 atau lebih baru dan paket OS `python3-venv`.

```bash
cd /path/to/apichecker
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e ".[dev]"
cp .env.example .env
```

Semua package Python dipasang ke `.venv`, bukan ke Python sistem.

Buat master key:

```bash
.venv/bin/python -m app generate-key
```

Masukkan hasilnya ke `APICHECKER_MASTER_KEY` dalam `.env`, lalu lindungi file tersebut:

```bash
chmod 600 .env
.venv/bin/alembic upgrade head
```

Master key digunakan untuk mengenkripsi dan membuka API key di database. Simpan backup
master key bersama backup database. API key lama tidak dapat dibuka jika master key hilang
atau berubah.

## Menjalankan

```bash
.venv/bin/python -m app
```

Entrypoint ini selalu bind ke `127.0.0.1`, port `8000` secara default. Ubah port melalui
`APICHECKER_PORT` di `.env` atau argumen `--port`.

Jangan menjalankan lebih dari satu worker karena scheduler berada di proses web yang sama.

### Script start dan stop

Jalankan satu script pengelola:

```bash
./scripts/apichecker
```

Script akan menampilkan pilihan:

1. Start
2. Stop
3. Status
4. Help
5. Exit

Perintah juga dapat diberikan langsung, misalnya
`./scripts/apichecker start`, `./scripts/apichecker stop`, atau
`./scripts/apichecker status`. Menu status menampilkan keadaan proses, PID,
IP loopback, port aktif, dan health check. Script menjalankan aplikasi di
background, menjalankan migrasi database sebelum start, menyimpan PID pada
`data/apichecker.pid`, dan menulis output ke `data/apichecker.log`.

## Penggunaan aplikasi

1. Buka menu **Providers** dan pilih **Add provider**.
2. Masukkan nama provider, base URL, dan API key jika diperlukan.
3. Jalankan **Check now** atau biarkan scheduler memeriksa sesuai interval.
4. Buka detail provider untuk melihat compatibility, model, capability, latency, dan histori.
5. Gunakan checkbox pada daftar provider untuk mengaktifkan atau menonaktifkan provider.
6. Gunakan menu **Settings** untuk mengatur monitoring otomatis global.

Saat monitoring otomatis global dimatikan, pemeriksaan terjadwal berhenti tetapi
`Check now` tetap dapat digunakan.

## Akses melalui SSH tunnel

Dari komputer lokal:

```bash
ssh -N -L 8080:127.0.0.1:8000 user@alamat-vps
```

Buka `http://127.0.0.1:8080`. Port 8000 tidak perlu dibuka di firewall VPS. Verifikasi dari
VPS dengan:

```bash
ss -ltnp | grep 8000
```

Alamat listener harus terlihat sebagai `127.0.0.1:8000`, bukan `0.0.0.0:8000`.

## Menjalankan dengan systemd

Salin dan sesuaikan [`deploy/apichecker.service`](deploy/apichecker.service), terutama nilai
`User`, `WorkingDirectory`, dan path pada `ExecStart`.

```bash
sudo cp deploy/apichecker.service /etc/systemd/system/apichecker.service
sudo systemctl daemon-reload
sudo systemctl enable --now apichecker
sudo systemctl status apichecker
```

Log:

```bash
journalctl -u apichecker -f
```

## Backup dan restore

Hentikan service sesaat agar file database konsisten:

```bash
sudo systemctl stop apichecker
cp data/apichecker.db /lokasi-backup/apichecker.db
cp .env /lokasi-backup/apichecker.env
sudo systemctl start apichecker
```

Untuk restore, hentikan service, kembalikan kedua file tersebut, pastikan `.env` memiliki
permission `600`, lalu jalankan service kembali.

## Pengujian

```bash
.venv/bin/pytest
```

Health check lokal:

```bash
curl http://127.0.0.1:8000/healthz
```

## Versioning dan release

Proyek menggunakan Semantic Versioning:

- `patch`: perbaikan bug atau perubahan kecil
- `minor`: fitur baru yang tetap kompatibel
- `major`: perubahan yang tidak kompatibel

Sebelum push perubahan aplikasi, siapkan release:

```bash
./scripts/prepare-release patch
```

Gunakan `minor` atau `major` sesuai jenis perubahan. Script akan memperbarui versi dan
tanggal release, membuat entri `CHANGELOG.md`, memvalidasi metadata, dan menjalankan test.

Setelah script selesai:

1. Ganti placeholder pada entri terbaru `CHANGELOG.md`.
2. Review perubahan menggunakan `git diff`.
3. Commit seluruh perubahan termasuk bump versi.
4. Push commit.

Aktifkan hook repository sekali setelah clone:

```bash
./scripts/install-hooks
```

Hook `pre-push` menjalankan validasi release dan test. Untuk push berikutnya ke branch
remote yang sudah memiliki aplikasi, push ditolak jika versi belum berubah.
GitHub Actions menjalankan pemeriksaan yang sama pada push dan pull request.

Validasi manual:

```bash
.venv/bin/python scripts/validate-release
```

Commit dan push tetap dilakukan secara eksplisit.

## Version history

| Version | Date | Ringkasan |
| --- | --- | --- |
<!-- version-history -->
| `0.4.0` | 2026-06-19 | Modal provider, label API key, interval baru, dan penyempurnaan dashboard |
| `0.3.0` | 2026-06-18 | Verifikasi provider, Notes persisten, dan ringkasan dashboard baru |
| `0.2.1` | 2026-06-18 | Settings, About, capability model, pagination, launcher hardening, dan release tooling |
| `0.2.0` | 2026-06-18 | Deteksi OpenAI/Anthropic, discovery model, scheduler, dan dashboard provider |
| `0.1.0` | 2026-06-18 | Implementasi awal FastAPI, SQLite, enkripsi, dan SSH tunnel |

Detail lengkap tersedia di [CHANGELOG.md](CHANGELOG.md).

## Konfigurasi

| Variable | Default | Keterangan |
| --- | --- | --- |
| `APICHECKER_MASTER_KEY` | wajib | Kunci Fernet untuk enkripsi API key |
| `APICHECKER_DATABASE_URL` | `sqlite:///./data/apichecker.db` | Lokasi database |
| `APICHECKER_PORT` | `8000` | Port loopback server |
| `APICHECKER_HISTORY_DAYS` | `30` | Retensi histori |
| `APICHECKER_MAX_CONCURRENT_CHECKS` | `5` | Batas pemeriksaan paralel |
| `APICHECKER_SCHEDULER_POLL_SECONDS` | `10` | Interval polling scheduler |

Endpoint dengan IP private, loopback, atau link-local ditolak untuk mengurangi risiko SSRF.
Redirect HTTP tidak diikuti agar credential tidak diteruskan ke host lain.

## Informasi

- Author: Ruby Rizwan
- Email: rzwan182@gmail.com
- Donasi: https://saweria.co/rubydevara
