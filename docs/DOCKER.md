# Deployment Docker ARIA

Panduan referensi untuk menjalankan ARIA menggunakan Docker dan Docker Compose.
Cakupan: arsitektur image, konfigurasi `compose.yaml`, variabel environment,
build manual, operasional harian, keamanan, dan troubleshooting.

Untuk panduan cepat lihat bagian [Menjalankan dengan Docker](../README.md#menjalankan-dengan-docker)
di README. Dokumen ini menjelaskan latar belakang teknis di balik konfigurasi tersebut.

## Prasyarat

- Docker Engine 24.0 atau lebih baru
- Docker Compose v2 (`docker compose` plugin, bukan `docker-compose` lama)
- Akses ke repository ARIA (Dockerfile, `compose.yaml`, `app/`, `alembic/`, `scripts/`)
- Ruang disk minimal 500 MB untuk image dan volume data

## Arsitektur image

Image dibangun dari [Dockerfile](../Dockerfile) dengan basis `python:3.12-slim`.
Tujuan utama: image kecil, berjalan sebagai non-root, root filesystem read-only,
dan tidak memiliki Linux capabilities.

### Lapisan image

Urutan layer dan alasannya:

1. **Base image** (`FROM python:3.12-slim`)
   - Slim variant Debian: cukup kecil (~150 MB) tanpa toolchain compiler yang tidak perlu.
   - Python 3.12 sesuai `requires-python = ">=3.12"` di `pyproject.toml`.

2. **Environment variables** (`ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 ...`)
   - `PYTHONDONTWRITEBYTECODE=1`: tidak menulis `.pyc` ke filesystem. Mengurangi write ke
     root filesystem read-only dan memperkecil image.
   - `PYTHONUNBUFFERED=1`: output log langsung ke stdout/stderr tanpa buffering, penting
     untuk `docker compose logs`.
   - `PIP_DISABLE_PIP_VERSION_CHECK=1` dan `PIP_NO_CACHE_DIR=1`: mempercepat build dan
     mengurangi ukuran layer.

3. **Non-root user** (`groupadd aria`, `useradd aria`)
   - User sistem `aria` (UID/GID dari sistem, tidak fixed) dengan home `/app` dan shell
     `/usr/sbin/nologin`. Container tidak pernah berjalan sebagai root.

4. **Instalasi aplikasi** (`COPY pyproject.toml README.md`, `COPY app ./app`, `pip install .`)
   - `pyproject.toml` dan `app/` di-copy sebelum `pip install .` agar dependency ter-install
     ke site-packages. README di-copy karena `pyproject.toml` merujuk `readme = "README.md"`.
   - Layer ini di-cache; perubahan kode `app/` memicu rebuild hanya dari titik ini.

5. **Migrasi dan script** (`COPY alembic.ini`, `COPY alembic`, `COPY scripts/...`)
   - `alembic.ini` dan direktori `alembic/` untuk auto-migration saat startup.
   - `scripts/docker-entrypoint` dipasang sebagai `/usr/local/bin/aria-entrypoint`.
   - `scripts/backup` dipasang sebagai `/usr/local/bin/aria-backup` untuk backup online.

6. **Permission dan ownership** (`chmod`, `mkdir`, `chown`)
   - Direktori `/app/data` dibuat dan di-`chown` ke `aria:aria` sebelum `USER aria`.
   - Entrypoint dan backup script di-`chmod 0755`.

7. **Switch user** (`USER aria`)
   - Semua perintah setelah ini (`EXPOSE`, `HEALTHCHECK`, `ENTRYPOINT`, `CMD`) berjalan
     sebagai `aria`.

8. **Healthcheck** (`HEALTHCHECK ... urlopen healthz`)
   - Memanggil endpoint `/healthz` setiap 30 detik. Docker menandai container `unhealthy`
     setelah 3 kegagalan berturut-turut.

9. **Entrypoint dan CMD** (`ENTRYPOINT ["aria-entrypoint"]`, `CMD ["python", "-m", "app"]`)
   - Entrypoint menjalankan migrasi Alembic, lalu `exec "$@"` menyerahkan ke CMD.
   - CMD `python -m app` membaca `app/__main__.py` yang menjalankan uvicorn.

### Entrypoint

[scripts/docker-entrypoint](../scripts/docker-entrypoint):

```sh
#!/usr/bin/env sh
set -eu

mkdir -p /app/data

echo "Applying ARIA database migrations..."
alembic upgrade head

echo "Starting ARIA container..."
exec "$@"
```

- `mkdir -p /app/data`: memastikan direktori data ada (volume mount point).
- `alembic upgrade head`: menjalankan semua migrasi sebelum aplikasi start.
- `exec "$@"`: menggantikan shell dengan proses aplikasi (PID 1) agar sinyal Docker
  (`SIGTERM`) diterima langsung oleh uvicorn untuk graceful shutdown.

## Konfigurasi compose.yaml

[compose.yaml](../compose.yaml) mendefinisikan satu service `aria`. Penjelasan field:

```yaml
services:
  aria:
    build:
      context: .
      dockerfile: Dockerfile
    image: aria:local
    container_name: aria
    init: true
    restart: unless-stopped
    read_only: true
    security_opt:
      - no-new-privileges:true
    cap_drop:
      - ALL
    env_file:
      - .env
    environment:
      APICHECKER_HOST: 0.0.0.0
      APICHECKER_PORT: 8000
      APICHECKER_SERVICE_MANAGER: disabled
      APICHECKER_DATABASE_URL: sqlite:////app/data/apichecker.db
    ports:
      - "127.0.0.1:${ARIA_PORT:-8000}:8000"
    volumes:
      - aria-data:/app/data
    tmpfs:
      - /tmp:size=64m,mode=1777
    stop_grace_period: 15s
    healthcheck:
      test: [CMD, python, -c, "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"]
      interval: 30s
      timeout: 3s
      start_period: 15s
      retries: 3

volumes:
  aria-data:
    name: aria-data
```

| Field | Nilai | Alasan |
| --- | --- | --- |
| `build.context` / `build.dockerfile` | `.`, `Dockerfile` | Build dari root repo menggunakan Dockerfile lokal. |
| `image` | `aria:local` | Tag lokal untuk membedakan dari image registry. |
| `container_name` | `aria` | Nama container fixed untuk kemudahan `docker compose exec`. |
| `init` | `true` | Menjalankan init (tini) sebagai PID 1 untuk reaping zombie dan forwarding sinyal. |
| `restart` | `unless-stopped` | Auto-restart pada crash atau reboot host, kecuali dihentikan manual. |
| `read_only` | `true` | Root filesystem container tidak bisa ditulis. Semua write harus ke volume atau tmpfs. |
| `security_opt` | `no-new-privileges:true` | Mencegah proses mendapat privilege baru via setuid binary. |
| `cap_drop` | `ALL` | Membuang semua Linux capabilities. Aplikasi tidak butuh salah satunya. |
| `env_file` | `.env` | Membaca `APICHECKER_MASTER_KEY` dan konfigurasi lain dari `.env`. |
| `environment.APICHECKER_HOST` | `0.0.0.0` | Bind ke semua interface di dalam container (network namespace terisolasi). |
| `environment.APICHECKER_PORT` | `8000` | Port aplikasi di dalam container. |
| `environment.APICHECKER_SERVICE_MANAGER` | `disabled` | Mematikan integrasi launcher/systemd yang tidak relevan di container. |
| `environment.APICHECKER_DATABASE_URL` | `sqlite:////app/data/apichecker.db` | Path absolut di dalam volume `aria-data`. Empat slash = absolute path. |
| `ports` | `127.0.0.1:${ARIA_PORT:-8000}:8000` | Hanya publish ke loopback VPS, tidak expose ke internet. Override via `ARIA_PORT`. |
| `volumes` | `aria-data:/app/data` | Named volume untuk persistensi database dan backup. |
| `tmpfs` | `/tmp:size=64m,mode=1777` | TMPFS untuk `/tmp` karena root filesystem read-only. Ukuran 64 MB cukup untuk operasi temporer. |
| `stop_grace_period` | `15s` | Waktu tunggu sebelum Docker mengirim `SIGKILL` setelah `SIGTERM`. Memberi waktu graceful shutdown. |
| `healthcheck` | `CMD python ... healthz` | Sama dengan HEALTHCHECK di Dockerfile, di-override di compose untuk eksplisit. |

### Volume `aria-data`

Named volume `aria-data` menyimpan:

- `apichecker.db` (database SQLite utama)
- `backups/` (direktori hasil backup)

Volume ini persisten lintas `docker compose down` / `up`. Hapus permanen dengan:

```bash
docker compose down -v
# atau
docker volume rm aria-data
```

### Port binding

Default: `127.0.0.1:8000` di host VPS. Tidak terbuka ke internet publik. Akses dari
komputer lokal melalui SSH tunnel:

```bash
ssh -N -L 8080:127.0.0.1:8000 user@alamat-vps
```

Lalu buka `http://localhost:8080` di browser.

Ganti port host tanpa edit `.env`:

```bash
ARIA_PORT=8010 docker compose up -d
```

## Variabel environment

Sumber: [app/config.py](../app/config.py), [.env.example](../.env.example).

| Variabel | Default | Keterangan |
| --- | --- | --- |
| `APICHECKER_MASTER_KEY` | (wajib) | Fernet key untuk enkripsi API key provider. Generate dengan `python -m app generate-key`. |
| `APICHECKER_DATABASE_URL` | `sqlite:///./data/apichecker.db` | URL database. Di container di-override ke `sqlite:////app/data/apichecker.db` (absolute path). |
| `APICHECKER_HOST` | `127.0.0.1` | Bind address. Di container di-override ke `0.0.0.0` agar accessible dari port mapping. |
| `APICHECKER_PORT` | `8000` | Port aplikasi. |
| `APICHECKER_SERVICE_MANAGER` | `launcher` | Mode service manager. Di container di-set `disabled` untuk mematikan launcher/systemd. |
| `APICHECKER_HISTORY_DAYS` | `30` | Retensi histori pemeriksaan (hari). |
| `APICHECKER_MAX_CONCURRENT_CHECKS` | `5` | Maksimum provider check yang berjalan paralel. |
| `APICHECKER_SCHEDULER_POLL_SECONDS` | `10` | Interval scheduler memeriksa provider yang due. |

Override di `.env` atau langsung di `compose.yaml` bagian `environment`. Nilai di
`environment` compose mengambil alih nilai `.env` untuk variabel yang sama.

## Build manual tanpa Compose

Tanpa Docker Compose, image bisa di-build dan di-run langsung:

```bash
# Build image
docker build -t aria:local .

# Generate master key (one-time)
docker run --rm aria:local python -m app generate-key

# Run container
docker run -d \
  --name aria \
  --init \
  --restart unless-stopped \
  --read-only \
  --security-opt no-new-privileges:true \
  --cap-drop ALL \
  --env-file .env \
  -e APICHECKER_HOST=0.0.0.0 \
  -e APICHECKER_PORT=8000 \
  -e APICHECKER_SERVICE_MANAGER=disabled \
  -e APICHECKER_DATABASE_URL=sqlite:////app/data/apichecker.db \
  -p 127.0.0.1:8000:8000 \
  -v aria-data:/app/data \
  --tmpfs /tmp:size=64m,mode=1777 \
  --stop-grace-period 15s \
  aria:local
```

Tanpa compose, `docker-entrypoint` tetap menjalankan `alembic upgrade head` sebelum start.

## Operasional

### Setup awal

1. Salin dan isi `.env`:

   ```bash
   cp .env.example .env
   ```

2. Generate Fernet master key:

   ```bash
   docker compose run --rm aria python -m app generate-key
   ```

   Masukkan output ke `APICHECKER_MASTER_KEY` di `.env`.

3. Build dan jalankan:

   ```bash
   docker compose build
   docker compose up -d
   ```

4. Verifikasi:

   ```bash
   docker compose ps
   docker compose logs -f aria
   ```

   Akses `http://localhost:8000` (melalui SSH tunnel jika di VPS).

### Migrasi database

Dijalankan otomatis oleh `docker-entrypoint` setiap container start:

```sh
alembic upgrade head
```

Tidak perlu menjalankan migrasi manual. Jika ingin memeriksa revision saat ini:

```bash
docker compose exec aria alembic current
```

### Backup database

Backup online menggunakan `aria-backup` (VACUUM-free, menggunakan `sqlite3.backup()`):

```bash
docker compose exec aria aria-backup --destination /app/data/backups --keep 14
```

- `--destination`: direktori tujuan di dalam container (di volume `aria-data`).
- `--keep`: jumlah backup yang dipertahankan, sisanya di-prune.

Salin backup ke host:

```bash
docker cp aria:/app/data/backups ./backups
```

Atau backup langsung ke host dengan mount temporary:

```bash
docker compose run --rm -v "$(pwd)/backups:/host-backups" aria \
  aria-backup --destination /host-backups --keep 14
```

### Restore database

1. Hentikan container:

   ```bash
   docker compose stop aria
   ```

2. Salin file backup ke volume:

   ```bash
   docker cp ./backups/apichecker-YYYYMMDD-HHMMSS/apichecker.db aria:/app/data/apichecker.db
   ```

3. Salin `.env` backup jika perlu:

   ```bash
   docker cp ./backups/apichecker-YYYYMMDD-HHMMSS/apichecker.env ./.env
   ```

4. Start container (entrypoint akan menjalankan migrasi):

   ```bash
   docker compose start aria
   ```

### Upgrade

Setelah menarik perubahan repository:

```bash
git pull
docker compose build --pull
docker compose up -d
```

- `--pull`: menarik base image terbaru dari registry.
- `up -d`: recreate container jika image berubah.
- Entrypoint menjalankan migrasi otomatis.

### Logs

```bash
# Follow log
docker compose logs -f aria

# 100 baris terakhir
docker compose logs --tail 100 aria

# Sejak waktu tertentu
docker compose logs --since 30m aria
```

### Restart dan stop

```bash
docker compose restart aria   # restart container
docker compose stop aria       # hentikan tanpa hapus
docker compose start aria      # start lagi
docker compose down            # hentikan dan hapus container (volume tetap)
docker compose down -v         # hentikan dan hapus container + volume
```

Tombol **Restart** di sidebar UI ARIA dinonaktifkan di container (`APICHECKER_SERVICE_MANAGER=disabled`).
Restart harus dilakukan dari host via Docker Compose.

## Keamanan dan batasan

### Keamanan

- **Non-root**: container berjalan sebagai user `aria`, bukan root.
- **Read-only root filesystem**: tidak ada write ke filesystem container kecuali volume
  `aria-data` dan tmpfs `/tmp`.
- **No capabilities**: `cap_drop: ALL` membuang semua Linux capabilities.
- **No new privileges**: `security_opt: no-new-privileges:true` mencegah privilege escalation.
- **Loopback-only port**: `127.0.0.1:8000` tidak expose ke internet. Akses via SSH tunnel.
- **API key terenkripsi**: `APICHECKER_MASTER_KEY` (Fernet) mengenkripsi API key provider
  sebelum disimpan ke database.
- **`.dockerignore`**: mengecualikan `.env`, `.venv`, database, log, dan artifact dari image.

### Batasan

- **Single replica**: scheduler berjalan di dalam proses web. Jalankan hanya satu container.
  Multiple replica akan menyebabkan scheduler bentrok.
- **No restart button**: tombol Restart UI dinonaktifkan di mode container.
- **SQLite only**: backup script (`scripts/backup`) saat ini hanya mendukung SQLite.
- **No auto-HTTPS**: container melayani HTTP plain. Terminasi TLS dilakukan di reverse proxy
  di luar container jika diperlukan.
- **Volume persistence**: `docker compose down` (tanpa `-v`) menjaga volume. `down -v`
  menghapus database permanen.

## Troubleshooting

### Container `unhealthy`

```bash
docker compose ps                    # cek status
docker compose logs --tail 50 aria   # cek error
docker compose exec aria python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"
```

Penyebab umum: `APICHECKER_MASTER_KEY` tidak valid atau kosong, migrasi Alembic gagal,
port bentrok dengan proses lain di host.

### Container tidak start / crash loop

```bash
docker compose logs aria
```

Periksa apakah `.env` ada dan `APICHECKER_MASTER_KEY` terisi. Entry point menjalankan
`alembic upgrade head` sebelum start; jika migrasi gagal, container exit.

### Database locked

Terjadi jika ada proses lain mengakses `apichecker.db` di volume yang sama, atau jika
container di-run bersamaan dengan deployment launcher/systemd di port yang sama. Pastikan
hanya satu instance ARIA yang berjalan.

```bash
docker compose exec aria ls -la /app/data/
```

### Permission denied saat write

Root filesystem read-only. Write hanya boleh ke `/app/data` (volume) atau `/tmp` (tmpfs).
Jika aplikasi mencoba write ke path lain, periksa apakah ada konfigurasi path yang perlu
di-override ke `/app/data`.

### Port sudah digunakan

```bash
# Cek proses di port 8000
ss -tlnp | grep :8000

# Gunakan port lain
ARIA_PORT=8010 docker compose up -d
```

### Volume penuh

```bash
docker system df
docker compose exec aria du -sh /app/data/*
```

Backup lama bisa di-prune:

```bash
docker compose exec aria aria-backup --destination /app/data/backups --keep 7
```

### Reset database (hapus semua data)

```bash
docker compose down -v
docker volume rm aria-data
docker compose up -d
```

Peringatan: ini menghapus semua provider, API key terenkripsi, dan histori. Tidak bisa diundo.

### Migrasi Alembic gagal

```bash
docker compose exec aria alembic history
docker compose exec aria alembic current
docker compose logs aria | grep -i alembic
```

Jika database corrupt dan tidak bisa dimigrasi, restore dari backup atau reset volume
(lihat "Reset database" di atas).

## Referensi

- [Dockerfile](../Dockerfile)
- [compose.yaml](../compose.yaml)
- [.dockerignore](../.dockerignore)
- [scripts/docker-entrypoint](../scripts/docker-entrypoint)
- [scripts/backup](../scripts/backup)
- [app/config.py](../app/config.py) - definisi `Settings` dan variabel environment
- [.env.example](../.env.example) - template konfigurasi
- [README.md - Menjalankan dengan Docker](../README.md#menjalankan-dengan-docker) - panduan cepat
- [Alembic documentation](https://alembic.sqlalchemy.org/) - referensi migrasi
