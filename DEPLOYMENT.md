# Deployment GitHub, Vercel, dan Supabase

## 1. Keamanan sebelum push

1. Revoke semua API key yang pernah dikirim melalui chat atau commit.
2. Pastikan `.env` tidak terlacak: `git check-ignore .env`.
3. Jalankan `git grep` untuk memastikan tidak ada key di file tracked.
4. Aktifkan GitHub secret scanning dan push protection jika tersedia.

## 2. Supabase

1. Buat project Supabase.
2. Buka SQL Editor dan jalankan berurutan `supabase/migrations/001_initial.sql`, lalu `supabase/migrations/002_admin_panel.sql`.
3. Di **Authentication > Users**, buat akun dengan email dan password.
4. Salin UUID user tersebut, kemudian tautkan profil lewat SQL Editor:

```sql
insert into public.admin_profiles(id, username, role)
values ('UUID_DARI_AUTH_USERS', 'admin@example.com', 'admin');
```

Gunakan role `expert` untuk teknisi yang hanya boleh memverifikasi kasus. Role `admin` juga dapat mengelola harga, dataset, dan registry model.

5. Ambil Project URL, anon key, dan service-role key dari pengaturan API.
6. Jangan memasukkan service-role key ke frontend atau GitHub.

7. Buka **Authentication > Settings > Password Security** dan aktifkan
   **Leaked password protection**. Pengaturan Auth ini dikelola melalui dashboard
   Supabase, bukan migration SQL.

Migration membuat:

- `diagnosis_history`: hasil diagnosis dan, bila diaktifkan, detail untuk verifikasi pakar;
- `feedback_cases`: kasus yang menunggu verifikasi teknisi;
- `model_versions`: metadata versi model;
- `api_rate_limits`: rate limiting terdistribusi.
- `admin_profiles`, `case_verifications`, `repair_prices`: akun ber-role, ground truth, dan harga;
- `training_datasets`, `training_jobs`, `audit_logs`: registry pipeline dan jejak perubahan.

Semua tabel memakai RLS tanpa policy publik. Backend memakai service-role key.
Migration `003_security_hardening.sql` menutup akses RPC `anon` dan
`authenticated` ke helper `SECURITY DEFINER` internal.

## 3. GitHub

Push repository setelah test lokal lulus. Workflow `.github/workflows/ci.yml` menjalankan compile, unit test, dan pemeriksaan pola secret pada setiap push/pull request.

## 4. Vercel

Import repository pada Vercel. Konfigurasi `vercel.json` sudah mengarahkan seluruh request ke entrypoint Flask `api/index.py`.

Di **Settings > Environment Variables**, masukkan nilai berikut untuk Production (dan Preview jika ingin diuji sebelum production):

```text
NLP_PROVIDER_CHAIN=sumopod,qwen
NLP_LOCAL_FALLBACK=false
NLP_PROVIDER_TIMEOUT_SECONDS=8
SUMOPOD_API_KEY=<key baru>
SUMOPOD_MODEL=glm-5
SUMOPOD_BASE_URL=https://ai.sumopod.com/v1
SUMOPOD_TIMEOUT_SECONDS=30
QWEN_API_KEY=<key baru>
QWEN_MODEL=qwen-plus
QWEN_BASE_URL=https://dashscope-intl.aliyuncs.com/compatible-mode/v1
SUPABASE_URL=<project URL>
SUPABASE_ANON_KEY=<anon key>
SUPABASE_SERVICE_ROLE_KEY=<service role key>
SUPABASE_TIMEOUT_SECONDS=5
SUPABASE_STORE_DIAGNOSIS_DETAILS=true
RATE_LIMIT_REQUESTS=20
RATE_LIMIT_WINDOW_SECONDS=60
RATE_LIMIT_SALT=<random minimal 32 karakter>
DATABASE_BACKEND=supabase
ADMIN_SESSION_SECRET=<random minimal 32 karakter>
SESSION_COOKIE_SECURE=true
```

Jangan menambahkan nilai rahasia ke berkas Git. Deploy ulang setelah mengubah environment variables.

`DATABASE_BACKEND` wajib bernilai `supabase`; jangan isi variabel `MYSQL_*` pada Vercel. Vercel adalah lingkungan serverless, sehingga operasi training dan aktivasi berkas model dari panel admin sengaja dinonaktifkan. Diagnosis, riwayat, verifikasi, harga, dan akun admin tetap menggunakan Supabase.

## 5. Smoke test setelah deploy

1. `GET /api/health` mengembalikan `status=ok`, backend `supabase`, dan `configured=true`.
2. Diagnosis hardware mengembalikan `model_scope=hardware_ds_pso`.
3. Periksa `persistence.persistent=true`.
4. Pastikan row baru muncul di `diagnosis_history` dan memiliki `case_verifications` berstatus `pending`.
5. Kirim feedback dan pastikan masuk `feedback_cases` dengan status `pending_expert_verification`.
6. Uji rate limit di environment preview, bukan production pelanggan.
7. Uji kegagalan Qwen menggunakan key yang sengaja tidak valid di Preview saja; diagnosis harus berhenti tanpa mapper lokal.

8. Buka `/admin/login`, masuk dengan email Supabase, lalu uji pemisahan hak `admin` dan `expert`.

9. Buka `/ready`; endpoint harus mengembalikan `status=ready`, `backend=supabase`, dan `reachable=true`.

Panel admin Vercel memakai Supabase. Training serta aktivasi berkas model dilakukan secara lokal karena filesystem serverless tidak persisten; deploy artefak tervalidasi melalui Git untuk menjadikannya model runtime produksi.

## 6. Rollback

Jika deployment bermasalah, gunakan deployment Vercel sebelumnya. Kandidat knowledge base dari folder `reports` tidak otomatis dipromosikan ke produksi; audit dataset dan persetujuan pakar tetap diperlukan.
