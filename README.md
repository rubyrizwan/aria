# ARIA

**API Reliability & Inference Analyzer**

Dashboard ringan untuk mendeteksi endpoint yang kompatibel dengan OpenAI atau Anthropic,
memeriksa API key, dan mengambil daftar model secara berkala. Aplikasi berjalan sebagai
satu proses FastAPI, menyimpan data di SQLite, dan mengenkripsi API key sebelum
menyimpannya.

Versi stabil saat ini: **1.0.4**. Fitur token usage belum termasuk dalam versi ini.

## Fitur

- Deteksi otomatis OpenAI-compatible dan Anthropic-compatible
- Penemuan model melalui `/v1/models` atau `/models`
- API key opsional untuk endpoint publik
- Interval per provider: 5, 15, 30, 60, atau 360 menit
- Dashboard status, compatibility, daftar model, latency, dan histori pemeriksaan
- Pencarian dan pagination model, maksimal 30 baris per halaman
- Indikator capability model dari metadata provider atau inferensi nama model
- Toggle monitoring per provider dan monitoring otomatis global
- Pemeriksaan manual menggunakan tombol `Load models`
- Pengujian akses inference per model dengan progress dan ringkasan hasil
- Katalog Available Models lintas provider dan histori inference
- Scheduled inference retest opsional dengan interval 24 jam, 3 hari, atau 7 hari
- Halaman Settings dan About
- API key terenkripsi dengan Fernet
- Retensi histori otomatis, default 30 hari
- Pengaturan global untuk mengaktifkan atau menonaktifkan monitoring otomatis
- UI responsif tanpa dependency frontend dari CDN
- Server default bind ke `127.0.0.1`; deployment container mengoverride bind internal
  ke `0.0.0.0` dan tetap publish hanya ke loopback VPS
- Backup SQLite online dan dukungan service `systemd --user`
- Deployment alternatif menggunakan Docker Compose

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

Entrypoint default bind ke `127.0.0.1`, port `8000`. Ubah melalui
`APICHECKER_HOST`, `APICHECKER_PORT`, atau argumen `--host` dan `--port`.

Jangan menjalankan lebih dari satu worker karena scheduler berada di proses web yang sama.

### Script start dan stop

Jalankan satu script pengelola:

```bash
./scripts/aria
```

The launcher displays these options:

1. Start ARIA
2. Stop ARIA
3. Restart ARIA
4. Show status
5. View logs
6. Help
7. Exit

Commands can also be executed directly, for example
`./scripts/aria start`, `./scripts/aria stop`, `./scripts/aria restart`,
`./scripts/aria status`, or `./scripts/aria logs`. Status includes health,
PID, uptime, bind address, service mode, database, runtime paths, and an SSH
tunnel example. The launcher applies database migrations before startup,
stores the PID in `data/apichecker.pid`, and writes output to
`data/apichecker.log`.

## Menjalankan dengan Docker

Panduan referensi lengkap: [docs/DOCKER.md](docs/DOCKER.md) (arsitektur image, konfigurasi `compose.yaml`, variabel environment, build manual, operasional, keamanan, dan troubleshooting).

Docker adalah opsi deployment alternatif. Jangan menjalankan deployment launcher,
systemd, dan Docker pada port host yang sama secara bersamaan.

Pastikan `.env` sudah tersedia dan `APICHECKER_MASTER_KEY` berisi Fernet key yang valid:

```bash
cp .env.example .env
docker compose run --rm aria python -m app generate-key
```

Masukkan key yang dihasilkan ke `.env`, kemudian build dan jalankan:

```bash
docker compose build
docker compose up -d
docker compose ps
docker compose logs -f aria
```

Compose menjalankan migrasi Alembic otomatis sebelum aplikasi dimulai. Database disimpan
pada named volume `aria-data`. Container berjalan sebagai non-root user, menggunakan
read-only root filesystem, dan tidak memiliki Linux capabilities.

Port container hanya dipublish ke loopback VPS:

```text
127.0.0.1:8000 -> container:8000
```

Gunakan SSH tunnel yang sama dari komputer lokal:

```bash
ssh -N -L 8080:127.0.0.1:8000 user@alamat-vps
```

Port host dapat diganti tanpa mengubah `.env`:

```bash
ARIA_PORT=8010 docker compose up -d
```

Perintah operasional:

```bash
docker compose restart aria
docker compose stop
docker compose start
docker compose down
```

Tombol Restart pada sidebar dinonaktifkan di container. Restart dilakukan dari host
menggunakan Docker Compose.

Backup database container:

```bash
docker compose exec aria aria-backup --destination /app/data/backups --keep 14
docker cp aria:/app/data/backups ./backups
```

Direktori backup di dalam container berada pada volume data yang sama. Salin hasilnya ke
host atau storage eksternal secara berkala.

Upgrade image setelah menarik perubahan repository:

```bash
docker compose build --pull
docker compose up -d
```

Gunakan hanya satu replica karena scheduler berjalan di dalam proses web.

## Penggunaan aplikasi

1. Buka menu **Providers** dan pilih **Add provider**.
2. Masukkan nama provider, base URL, dan API key jika diperlukan.
3. Jalankan **Load models** atau biarkan scheduler memeriksa sesuai interval.
4. Buka detail provider untuk melihat compatibility, model, capability, latency, dan histori.
5. Gunakan checkbox pada daftar provider untuk mengaktifkan atau menonaktifkan provider.
6. Gunakan menu **Settings** untuk mengatur monitoring otomatis global dan scheduled inference retest.

Saat monitoring otomatis global dimatikan, pemeriksaan terjadwal berhenti tetapi
`Load models` tetap dapat digunakan. Scheduled inference retest default-nya nonaktif
karena mengirim request inference nyata dan dapat memakai kuota atau credit provider.

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

Untuk VPS, gunakan user service yang dibuat dari path repository saat ini:

```bash
./scripts/aria stop
./scripts/install-user-service
systemctl --user status apichecker.service
```

Installer juga mengaktifkan backup harian dengan retensi 14 backup. Agar user service tetap
aktif setelah logout dan otomatis berjalan setelah reboot, administrator VPS dapat menjalankan:

```bash
sudo loginctl enable-linger "$USER"
```

Perintah operasional:

```bash
systemctl --user restart apichecker.service
systemctl --user stop apichecker.service
journalctl --user -u apichecker.service -f
systemctl --user list-timers apichecker-backup.timer
```

Tombol Restart di sidebar menggunakan service manager ini jika
`APICHECKER_SERVICE_MANAGER=systemd-user`. Request restart dilindungi token yang diturunkan
dari master key. Unit system-level legacy pada `deploy/apichecker.service` menonaktifkan
tombol ini agar tidak membutuhkan aturan `sudo` tambahan.

## Backup dan restore

Backup SQLite konsisten dapat dibuat saat aplikasi tetap berjalan:

```bash
./scripts/backup
./scripts/backup --destination /lokasi-backup --keep 30
```

Setiap backup berisi `apichecker.db` dan `apichecker.env`. File environment wajib disimpan
bersama database karena berisi master key untuk membuka API key terenkripsi.

Untuk restore:

```bash
systemctl --user stop apichecker.service
cp /lokasi-backup/apichecker.db data/apichecker.db
cp /lokasi-backup/apichecker.env .env
chmod 600 .env data/apichecker.db
systemctl --user start apichecker.service
```

Jika tetap memakai launcher, pasang rotasi `data/apichecker.log`:

```bash
./scripts/install-logrotate
```

Deployment `systemd --user` menulis log ke journal sehingga tidak membutuhkan logrotate
untuk log aplikasi.

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
| `1.0.4` | 2026-06-23 | Release tooling menjaga versi stabil README tetap sinkron otomatis |
| `1.0.3` | 2026-06-21 | Dokumentasi referensi deployment Docker di `docs/DOCKER.md` |
| `1.0.2` | 2026-06-20 | See CHANGELOG.md; update this summary before committing |
| `1.0.1` | 2026-06-20 | Perbaikan deteksi proses launcher setelah direktori repository dipindahkan |
| `1.0.0` | 2026-06-20 | Rilis stabil pertama ARIA untuk monitoring provider, model discovery, dan inference access |
| `0.4.3` | 2026-06-19 | Perubahan branding aplikasi menjadi ARIA: API Reliability & Inference Analyzer |
| `0.4.2` | 2026-06-19 | Katalog model lintas provider, histori inference, dashboard operasional, backup, scheduled retest, dan service controls |
| `0.4.1` | 2026-06-19 | Pengujian akses model, progress inference, filter hasil, status monitoring, dan latency inference |
| `0.4.0` | 2026-06-19 | Modal provider, label API key, interval baru, dan penyempurnaan dashboard |
| `0.3.1` | 2026-06-18 | Perbaikan kompatibilitas database pada revision Alembic `0005` |
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
| `APICHECKER_HOST` | `127.0.0.1` | Bind address aplikasi; Compose mengoverride ke `0.0.0.0` di dalam container |
| `APICHECKER_PORT` | `8000` | Port loopback server |
| `APICHECKER_SERVICE_MANAGER` | `launcher` | Mekanisme restart: `launcher` atau `systemd-user` |
| `APICHECKER_HISTORY_DAYS` | `30` | Retensi histori |
| `APICHECKER_MAX_CONCURRENT_CHECKS` | `5` | Batas pemeriksaan paralel |
| `APICHECKER_SCHEDULER_POLL_SECONDS` | `10` | Interval polling scheduler |

Endpoint dengan IP private, loopback, atau link-local ditolak untuk mengurangi risiko SSRF.
Redirect HTTP tidak diikuti agar credential tidak diteruskan ke host lain.

## Informasi

- Author: Ruby Rizwan
- Email: rzwan182@gmail.com
- Donasi: https://saweria.co/rubydevara
