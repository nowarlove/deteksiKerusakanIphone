# Sistem Pakar Kerusakan iPhone DS-PSO

Aplikasi diagnosis hardware iPhone dengan mesin inferensi Dempster-Shafer dan belief hasil optimasi Particle Swarm Optimization. NLP hanya menerjemahkan keluhan bebas menjadi 13 fitur terstruktur; keputusan diagnosis tetap dibuat oleh DS.

## Menjalankan aplikasi

```bash
python -m venv .venv
. .venv/Scripts/activate
pip install -r requirements.txt
cp .env.example .env
python -m app.app
```

Alur memakai provider NLP terkonfigurasi untuk memahami setiap keluhan dan tidak melakukan pemetaan NLP lokal.

## Provider NLP opsional

Atur provider utama dan fallback di `.env`:

```dotenv
NLP_PROVIDER_CHAIN=sumopod,qwen
NLP_LOCAL_FALLBACK=false
```

- `sumopod`: API OpenAI-compatible, default model `glm-5`.
- `qwen`: Alibaba Cloud Model Studio/DashScope API, default `qwen-plus`.

Jika provider utama gagal atau limit, provider berikutnya pada chain dicoba otomatis. Jika semua gagal atau tidak menemukan evidence DS-PSO, diagnosis dihentikan dan pengguna diminta mencoba kembali atau memperjelas keluhan. Sistem tidak menebak fitur dengan regex lokal. Kunci hanya disimpan di `.env` lokal atau Environment Variables Render dan tidak boleh dimasukkan ke Git.

Setiap keluhan hardware melalui alur `provider NLP → validasi schema dataset → konfirmasi pengguna → DS-PSO`. Schema, nilai default, domain kategorikal, dan 18 pasangan evidence diturunkan otomatis dari CSV train/test, `belief.json`, dan `optimal_knowledge_base.json`. Baris mentah dataset tidak dikirim ke layanan eksternal.

## Menjalankan test

```bash
python -m unittest discover -s tests -v
```

## MySQL XAMPP dan admin panel

1. Jalankan Apache/MySQL dari XAMPP.
2. Import `mysql/001_initial.sql` melalui phpMyAdmin.
3. Salin `mysql/002_create_local_user.example.sql`, ganti password, lalu jalankan melalui phpMyAdmin sebagai root.
4. Isi password yang sama pada `MYSQL_PASSWORD` di `.env` dan ganti `ADMIN_SESSION_SECRET` dengan nilai acak panjang.
5. Buat akun web admin dari Git Bash:

```bash
python research/manage_admin.py admin --role admin
```

6. Restart aplikasi dan buka `http://localhost:5000/admin/login`.

Admin panel menyediakan riwayat diagnosis, verifikasi ground truth, harga dinamis, dataset staging, training jobs, model registry, aktivasi, dan rollback. Hanya kasus berstatus `verified` yang dapat ditambahkan ke dataset training. Prediksi sistem tidak pernah otomatis menjadi label aktual.

Training berjalan sebagai background thread lokal dan memakai command yang telah dibatasi. Jangan menjalankan training pada web service Render; gunakan worker lokal atau layanan worker terpisah.

## Admin Supabase di Render

Jalankan kedua migration pada folder `supabase/migrations`, buat user melalui Supabase Auth, kemudian tambahkan UUID user itu ke `admin_profiles`. Login produksi menggunakan email Supabase; password tidak disimpan oleh tabel aplikasi. Role `expert` dapat membaca dan memverifikasi diagnosis, sedangkan pengelolaan harga dan registry dibatasi untuk role `admin`.

Set `DATABASE_BACKEND=supabase`, `SUPABASE_URL`, `SUPABASE_ANON_KEY`, dan `SUPABASE_SERVICE_ROLE_KEY` hanya pada environment backend Render. Petunjuk lengkap ada di `DEPLOYMENT.md`.

## Eksperimen penelitian

Instal dependensi penelitian secara terpisah agar deployment web tetap ringan:

```bash
pip install -r requirements-research.txt
python research/run_preprocessing.py
python research/run_experiment.py
```

Skrip mencari CSV terbaru di `data/dataLatih` dan `data/dataUji`, kemudian:

1. mengevaluasi DS standar dengan belief pakar pada test set;
2. menjalankan PSO pada train set untuk beberapa seed;
3. mengevaluasi DS-PSO pada test set yang sama;
4. menyimpan metrik multiclass, confusion matrix, dan riwayat konvergensi;
5. menghasilkan kandidat model di `reports/optimal_knowledge_base_candidate.json`.

Kandidat tidak otomatis mengganti model produksi. Periksa audit dataset dan hasil evaluasi terlebih dahulu.

## Catatan metodologi

- Kasus software difilter di luar ruang lingkup evaluasi DS-PSO.
- Alternatif diagnosis adalah kandidat top-N, bukan klaim multilabel.
- Feedback masuk antrean verifikasi dan tidak langsung mengubah prior atau belief.
- Filesystem Render tidak digunakan sebagai database persisten.

## Deployment

Deployment GitHub, Render, migration Supabase, environment variables, smoke test, dan rollback dijelaskan di `DEPLOYMENT.md`. Sistem tetap berjalan tanpa Supabase, tetapi persistence feedback dan rate limit terdistribusi baru aktif setelah konfigurasi Supabase tersedia.
