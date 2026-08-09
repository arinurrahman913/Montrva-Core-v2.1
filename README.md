# AlphaForge Core v2

Implementasi kode AlphaForge v2 — dua mesin analisis saham, plus satu lapisan pribadi opsional di atasnya:

- **Layer 1 — Market Context Engine**: 13 komponen makro (yield curve, VIX, likuiditas, credit spread, dll) → satu skor kondisi pasar (`Layer Score`).
- **Layer 2 — Stock Analysis Engine**: 10 tahap per saham (Screening → Evidence → Knowledge → Catalyst → Peer → Confidence → Risk → Reasoning → Aggregator → Historical), ~4.000 ticker per run.
- **Lapisan Pribadi** (`alphaforge/personal/`, bisa dihapus utuh): mengubah temuan Layer 2 jadi *call* bernama, lalu menagih pertanggungjawabannya lewat vonis & rapor kalibrasi.

Spec/arsitektur lengkap (kenapa sistem ini dirancang begini) ada di repo terpisah [`alphaforge-v2-main`](https://github.com/arinurrahman913/alphaforge-v2). Repo ini adalah **implementasinya** — kalau spec dan kode berbeda, catat perbedaannya (ada beberapa yang didokumentasikan sengaja, lihat §Known Gaps), jangan langsung asumsikan salah satu paling benar.

**Kalau kamu AI/kontributor baru:** baca §Arsitektur & §Data Contracts dulu sebelum menyentuh kode — bagian itu isinya "kenapa ini begini", bukan cuma "ini ada apa". Kalau yang akan kamu sentuh menyangkut backend atau berkas di `dashboard/data/`, **§Memori wajib dibaca juga** (berkasnya ratusan MB; memuatnya secara naif mengembalikan 2,7 GB RAM). Untuk **cara menjalankan sehari-hari** (refresh data, baca dashboard, troubleshooting), semuanya di [`WORKFLOW.md`](WORKFLOW.md) — jangan diduplikasi di sini.

**Satu sikap yang menjelaskan banyak keputusan di repo ini:** kalau sebuah angka belum terbukti, sistemnya diminta mengatakan itu — bukan menyembunyikannya di balik angka yang terlihat rapi. Karena itu ada gerbang bukti yang menolak dirinya sendiri, flag `undetermined` yang dibedakan dari "bersih", dan larangan eksplisit atas skor tunggal di Aggregator. Kalau kamu tergoda menyederhanakan salah satunya jadi satu angka enak dibaca, itu bukan penyederhanaan — itu membuang informasi yang paling mahal didapat.

---

## Arsitektur

Tiga bagian yang jalan terpisah tapi saling terhubung lewat file JSON:

```
alphaforge/          → mesin analisis murni (Python). Tidak tahu apa-apa soal web/dashboard.
  layer1/             13 komponen Market Context + benchmark_history.py (deret ^GSPC lintas run)
  layer2/              tahap Stock Analysis (contracts.py per tahap + sources/ untuk fetch eksternal)
  personal/            lapisan pribadi OPSIONAL — lihat §Lapisan Pribadi. Kalau folder ini dihapus,
                       sisa sistem jalan utuh (backend mendeteksinya lewat PERSONAL_ENABLED).
  cli.py               CLI untuk jalankan tiap tahap manual (python -m alphaforge.cli <stage>)
  cache.py             cache lokal berbasis file (.cache/, gitignored, TTL per sumber)
  runlock.py           kunci eksklusif antar-proses — mencegah dua run menulis file tahap bersamaan
  json_safe.py         serialisasi JSON yang aman dari NaN/Infinity (lihat §Known Gaps)

backend/app.py        → Flask, READ-ONLY. Cuma menyajikan dashboard/data/*.json sebagai API.
                         Tidak menghitung apa pun sendiri. Juga bisa TRIGGER refresh (subprocess ke
                         scripts/) lewat tombol Generate, tapi computation-nya tetap di alphaforge/.
  big_json.py           akses per-record ke dua berkas terbesar tanpa menahannya di RAM —
                         WAJIB dibaca sebelum menambah pembaca stage baru, lihat §Memori.
  personal_routes.py    route lapisan pribadi (didaftarkan cuma kalau alphaforge/personal/ ada).

frontend/             → React + Vite. Baca API dari backend/app.py, render dashboard.
                         Build ke frontend/dist/ (tracked di git, di-serve langsung oleh Flask —
                         jadi produksi cuma butuh 1 proses: python backend/app.py).

scripts/               → orkestrasi/otomasi: refresh_full_pipeline.py (seluruh rantai, all-or-nothing),
                         refresh_layer1.py (cepat, Layer 1 saja), build_sector_map.py (klasifikasi
                         sektor per ticker, cache 90 hari, dipakai screening per-sektor),
                         start-backend-hidden.vbs (jalankan backend tanpa jendela, untuk auto-start),
                         plus beberapa backfill sekali-pakai yang idempoten.

dashboard/data/*.json  → SUMBER KEBENARAN untuk apa yang ditampilkan dashboard (gitignored — hasil
                         generate, bukan kode). Kalau angka di dashboard salah, cek isi file ini
                         dulu sebelum curiga ke frontend. Ukurannya ratusan MB, bukan kilobyte —
                         perlakukan sesuai (§Memori).
```

**Alur data**: `alphaforge/` (compute) → tulis ke `dashboard/data/*.json` (lewat `scripts/refresh_*.py` atau `python -m alphaforge.cli <stage> --out ...`) → `backend/app.py` baca file itu → `frontend/` render. Tidak ada arah lain — dashboard **tidak pernah** menghitung ulang apa pun (Prinsip 2.1 di spec repo).

---

## Setup & Menjalankan

Ringkas (detail lengkap + troubleshooting ada di [`WORKFLOW.md`](WORKFLOW.md)):

```powershell
pip install -r requirements.txt
npm --prefix frontend install
Copy-Item .env.example .env   # isi FRED_API_KEY (gratis, lihat komentar di file)

# refresh data (pilih salah satu)
python scripts/refresh_layer1.py          # cepat (~1 menit), Layer 1 saja
python scripts/refresh_full_pipeline.py   # lengkap, semua stage — lihat catatan durasi di bawah

# jalankan dashboard
npm --prefix frontend run build           # build sekali (atau `npm run dev` untuk mode dev)
python backend/app.py                     # buka http://localhost:5000
```

Tanpa `FRED_API_KEY`, 5 komponen Layer 1 berbasis FRED otomatis `status=missing` — pipeline tetap jalan, tidak crash. Sama halnya tanpa `FINNHUB_API_KEY` (juga di `.env`, gratis di [finnhub.io/register](https://finnhub.io/register)): Evidence.news otomatis `status=missing` untuk semua ticker, bukan crash.

**Durasi run penuh: 1j45m–4j20m, dan yang menentukan adalah JARAK dari run sebelumnya.** `FUNDAMENTAL_CACHE_TTL` / `YAHOO_INFO_CACHE_TTL` / `OWNERSHIP_CACHE_TTL` semuanya 24 jam, jadi run yang dimulai <24 jam setelah run sebelumnya memakai ulang cache `.info` secara sah dan tidak pernah menyentuh lantai throttle Finnhub (~1,05 dtk/call). Terukur: 1j44m (jarak ~16 jam) vs ~3 jam (jarak >24 jam). **Jangan baca run cepat sebagai "Evidence membaik", dan jangan kaget kalau run berikutnya kembali ~3 jam.** Long pole selalu Screening + Evidence; sisanya hitungan detik, kecuali blok tulis di akhir (ratusan MB).

**Jangan klik Generate saat ada run manual berjalan.** `runlock.py` menolak pendatang kedua (tidak mengantre — untuk job terjadwal, mengantre justru salah), dan kunci basi ditangani otomatis lewat cek pid + umur, jadi crash tidak mengunci sistem selamanya. Sebelum runlock ada, tabrakan seperti ini pernah mengorupsi 55 berkas cache dan membuat `money_flow` jatuh ke `missing`.

**Backend tidak butuh restart setelah run baru** — `_get_stage` reload berbasis mtime. Restart cuma perlu kalau `backend/*.py` sendiri berubah (`pythonw` tidak punya autoreload). Backend bind port ~40 detik setelah start karena warm cache; itu normal, bukan tanda gagal.

---

## Layer 1 — Market Context Engine

13 komponen, semua sudah live: yield curve, volatility index (VIX), currency/DXY, commodity signals, market regime, sector rotation, liquidity conditions, macro calendar, business cycle stage, money flow, market breadth, market sentiment, credit spread.

5 komponen butuh `FRED_API_KEY` (yield curve, liquidity, macro calendar, business cycle, credit spread). `market_breadth` butuh Screening pernah jalan minimal sekali (pakai cache harga hasil Screening). `market_sentiment` `ok` dengan ≥3/6 input — 4 otomatis (VIX, breadth, CFTC COT, FINRA short-volume), 2 sisanya (put/call, AAII) manual opsional.

Detail cara baca tiap komponen + anatomi kartu ada di [`WORKFLOW.md`](WORKFLOW.md) §5.

---

## Layer 2 — Stock Analysis Engine

Tahap berurutan, tiap tahap konsumsi output tahap sebelumnya. Angka contoh dari run 2026-08-08 (4.054 ticker lolos dari 5.248 dipindai):

| # | Tahap | Modul | Ringkas |
|---|---|---|---|
| 1 | Screening | `screening.py` | Filter universe NASDAQ+NYSE (~8.500 raw → ~5.200 setelah cheap-filter) jadi kandidat: exclude ETF/test-issue, market cap/likuiditas/harga minimum. Soft-flag (micro-cap, recent-IPO, ADR) tetap lolos. |
| 2 | Evidence | `evidence.py` + `sources/` | Kumpulkan fakta mentah per ticker: price/OHLCV (Yahoo), fundamental (Yahoo), institutional ownership % + top holders (Yahoo), **institutional/insider activity dari SEC Form 4** (`sources/sec_form4.py` — lihat catatan di bawah), news (Finnhub), SEC filings 10-K/10-Q/8-K (EDGAR, jendela 3 tahun). |
| 3 | Knowledge | `knowledge.py` | Strukturkan Evidence jadi `KnowledgeProfile` 7-bagian (identity, financial health, competitive structure/momentum, historical trend, ownership, valuation, governance) — murni faktual, tanpa penilaian kualitatif. |
| 4 | Catalyst | `catalyst.py` | `CatalystSet` per ticker — jadwal earnings & ex-dividend dari Yahoo `.info` yang **sudah di-cache** Evidence (nol call jaringan baru). Katalis produk/regulator/rumor butuh parsing news, belum ada. |
| 5 | Peer | `peer.py` | Posisi percentile vs peer sektor (P/E, P/S, margins, dll), min 3 peer **yang punya nilai valid untuk metrik itu** (bukan sekadar ukuran roster). |
| 6 | Confidence | `confidence.py` | `ConfidenceReport`: skor kualitas data per section Knowledge (0-100) + limiters, bukan lagi single confidence_score generik. Ikut menghukum Layer 1 yang degraded (`context_penalty`). |
| 7 | Risk | `risk.py` | `RiskAssessment` dengan `Flag` (severity `tinggi`/`ekstrem`, status `triggered`/`undetermined`). Flag `ekstrem` yang `triggered` **hard-gate**: `halted=true`, ticker itu skip tahap Reasoning. |
| 8 | Reasoning | `reasoning.py` | **3 lensa independen, TIDAK diagregasi jadi satu angka** (`ModuleOutput` per lensa — lihat §Data Contracts). |
| 9 | Aggregator | `aggregator.py` | Gabungkan 3 `ModuleOutput` + `Synthesis` (peta konvergensi, bukan skor tunggal). |
| 10 | Historical | `historical.py` | Simpan snapshot utuh `AggregatorOutput` per hari per ticker (`HistoricalEntry`). `outcome` sengaja `None` selamanya di lapisan publik — evaluasi ada di lapisan pribadi, lihat §Lapisan Pribadi. |

Tahap tambahan yang menulis berkasnya sendiri di run yang sama: **Layer 1** (dijalankan lebih awal karena Confidence membutuhkannya), **Benchmark** (deret ^GSPC lintas run — `spx_history` cuma 90 bar bergulir, tidak cukup untuk horizon panjang), **Price target** (snapshot target analis harian), **Institutional flow** (arah aliran dana 13F per sektor), dan **Personal**.

**`halted` bukan teori.** Run 2026-08-08 menghentikan 5 ticker, semuanya karena 8-K item 1.03 (bangkrut/receivership) dalam 2 tahun. Kalau jumlah Reasoning lebih kecil dari Aggregator, **selisihnya adalah ticker yang di-halt, bukan bug** — `AggregatorOutput`-nya tetap ada lengkap dengan `risk_flags`, cuma tanpa 3 lensa.

### Data Contracts — penting dibaca sebelum ubah Reasoning/Aggregator

Repo ini sudah melalui **rewrite Data-Contracts v3.0.0** (2026-07-22/23) yang mengganti arsitektur lama (single `conviction_score` + `strong_buy`/…/`strong_sell` + `FinalRecommendation`) dengan yang jauh lebih ketat:

- **`ModuleOutput`** (`reasoning_contracts.py`) — tiap lensa (`multibagger`, `quality_compound`, `speculative`) punya **kosakata stance sendiri**, bukan enum bersama:
  - Multibagger: `ruang_terbuka` / `ruang_sempit` / `ruang_tertutup` / `ruang_tak_terbaca`
  - Quality: `compounding_kuat` / `compounding_rapuh` / `bukan_compounder` / `mesin_tak_terbaca`
  - Speculative: `asimetri_berkatalis` / `asimetri_tanpa_katalis` / `tanpa_asimetri` / `asimetri_tak_terbaca`
  - `confidence` terpisah dari `stance` (bukan dicampur jadi satu angka). `validate_module_output()` menjalankan cek V1-V6 tiap pipeline (di-log, tidak menghentikan run).
- **`AggregatorOutput`** (`aggregator_contracts.py`) — **DILARANG** (D-04) punya field verdict/score/rank/recommendation tunggal. Isinya `module_outputs` (3 `ModuleOutput` apa adanya, berdampingan) + `Synthesis` (agreements/divergences/`surprise`, confidence = **terendah** dari 3 modul, bukan rata-rata). `halted=true` → `module_outputs` kosong, `synthesis=None`, tapi `risk_flags` tetap terisi.
- **`HistoricalEntry`** (`historical_contracts.py`) — simpan **snapshot utuh** `AggregatorOutput`, bukan ringkasan. `outcome` sengaja `None` (evaluasi v2.1 belum diputuskan bentuknya).

**Kalau kamu lihat kode/dokumen lama menyebut `conviction_score`, `strong_buy`, atau `FinalRecommendation` — itu SUDAH DIGANTI.** Jangan tambahkan balik pola itu.

### SEC Form 4 — Institutional/Insider Activity

`sources/sec_form4.py` fetch daftar Form 4 filing dari SEC EDGAR submissions API per ticker, disimpan sebagai `InstitutionalActivity` (`contracts.py`) di Evidence, lalu diringkas jadi `Ownership.insider_filing_activity_30d` (hitungan filing 30 hari terakhir) di Knowledge.

**Batasan yang harus diketahui**: ini **MVP — cuma hitungan filing, bukan parsing arah transaksi** (belum bisa bedakan insider beli vs jual, atau berapa lembar saham). Percobaan parsing XML Form 4 penuh gagal karena struktur path dokumen SEC archive tidak konsisten (404 di banyak kasus) — didokumentasikan sebagai keterbatasan yang disengaja, bukan bug tersembunyi. Sinyal ini dipakai sebagai proxy "ada insider terlibat" di 2 lensa Reasoning:
- **Quality**: +8/+15/+20 poin tergantung jumlah filing (1/2/3+)
- **Speculative**: 2+ filing dalam 30 hari **memicu** stance `asimetri_berkatalis` (diperlakukan sebagai katalis implisit — insider tidak akan filing kalau tidak melihat upside)

### Dashboard: Sector Cards (Knowledge view)

Halaman Knowledge di dashboard **tidak** menampilkan satu tabel flat 4000+ baris — tapi grid card per sektor (klik untuk expand ke tabel penuh sektor itu). Agregat per sektor (leader, opportunity count, risk flag count, dll) dihitung di endpoint backend `GET /api/knowledge/sector-summary` (`backend/app.py`), bukan di browser — karena butuh join `knowledge.json` dengan `reasoning_outputs.json`/`risk_assessments.json` yang ukurannya puluhan MB.

**Catatan penting kalau menambah statistik agregat baru di situ**: `return_1y`/`pe_ratio`/`revenue_yoy` semuanya *fat-tailed* (satu ticker naik ribuan persen menyeret rata-rata jauh dari kondisi tipikal) — pakai **median**, bukan mean, untuk metrik itu (lihat `_median()` di `backend/app.py`). `institutional_pct` aman pakai mean (dibatasi 0–100%). Nama sektor di data pakai taksonomi **Yahoo Finance mentah** (`Financial Services`, `Consumer Cyclical`, `Consumer Defensive`, `Basic Materials`), **bukan** nama GICS baku yang mirip tapi beda — jangan asumsikan `sector` field sudah GICS-clean.

---

## Lapisan Pribadi (`alphaforge/personal/`, OPSIONAL)

Layer 2 di atas berhenti di "apa yang terlihat" dan sengaja tidak pernah menyuruh berbuat apa-apa. Lapisan pribadi adalah yang mengubahnya jadi *call* bernama, lalu **menagih pertanggungjawabannya**. Ia hidup terpisah supaya bisa dihapus utuh untuk rilis publik: kalau folder ini tidak ada, `PERSONAL_ENABLED=False`, route-nya tidak didaftarkan, dan grup nav "Pribadi" hilang sendiri dari dashboard.

Tiga halaman dashboard: **Agregator Pribadi** (top pick per lensa hari ini), **Riwayat Pribadi** (jejak tiap ticker + vonis), **Rapor Kalibrasi** (apakah sistem ini terbukti atau belum).

Yang penting dipahami sebelum menyentuh bagian ini:

- **Tiap call punya `claim_type`, dan itu menentukan bagaimana ia dinilai.** `arah` (naik ≥ target) dinilai beda dari `amplitudo` (bergerak melebihi derunya sendiri, arah tidak diklaim). Tanpa pemisahan ini, kalibrasi menjumlahkan dua jenis kebenaran jadi satu angka tak bermakna.
- **Lensa Spekulatif mendeteksi PERISTIWA, bukan ARAH.** Diukur lawan pembanding acak di jendela yang sama: kemampuan menebak "akan bergerak besar" **+15,5pp (SK95% +10,0..+21,0 — di luar derau)**, kemampuan menebak arahnya **+4,2pp (SK95% −2,8..+11,1 — di dalam derau)**. Karena itu action-nya bernama `siaga_gerakan`, bukan `masuk_spekulatif`; klaimnya amplitudo. **Kalau nanti ada metrik yang naik "signifikan", cek dulu apakah metrik kebalikannya ikut naik** — di sini 'terbukti' dan 'meleset' naik bersamaan, dan itulah yang membongkar bahwa yang terdeteksi amplitudo.
- **Vonis v2 memakai z-score**, bukan ambang persen tetap: `z = (return − return_indeks) / (σ_harian × √bar_bursa)`, σ dari 60 bar SEBELUM entry (memakai jendelanya sendiri = mengintip masa depan). Alasannya struktural — gerakan khas di jendela 4-9 hari ±5,16% sementara target lamanya 3%, jadi **targetnya di bawah derau** dan "hit rate 34%" ternyata cuma base rate pasar, bukan sinyal.
- **Gerbang bukti ada DUA dan keduanya harus lolos**: n ≥ 30 tesis **dan** ≥ 5 tanggal masuk berbeda (`MIN_THESES` / `MIN_ENTRY_DATES` di `personal_calibration.py`). Yang kedua yang mengikat — 167 tesis pertama lahir cuma dari 2 tanggal masuk, artinya 2 taruhan disebar ke ~85 ticker, bukan 167 sampel independen. Irisan yang gagal gerbang **tetap ditampilkan lengkap dengan angkanya** (diredupkan + alasannya), sengaja tidak disembunyikan.
- **Blok `mechanical_tuning` memeriksa dirinya sendiri tiap build.** Selama `allowed=false`, **tidak ada** threshold/bobot/gerbang skor yang boleh disetel berdasarkan riwayat. Itu kode yang menjaganya, bukan janji di dokumen.
- **Apa pun yang membandingkan action lintas waktu WAJIB lewat alias** (`ACTION_ALIASES` di `personal_contracts.py`, kembarannya `BEST_ACTION_ALIASES` di `frontend/src/format.js` — **harus berubah bersama**). Tanpa itu, hari pergantian nama action terbaca sebagai "semua keluar + semua baru", dan setiap streak ter-reset ke 1.

Vonis untuk lensa Multibagger & Quality **belum mungkin ada sampai 2027/2028** (horizon tesisnya). Blok `not_yet_evaluable` di rapor melaporkan tanggal paling cepatnya — tanpa itu, rapor terbaca seolah sistem sudah terkalibrasi padahal yang terukur baru 1 dari 3 lensa.

---

## Memori & berkas besar — baca sebelum menambah pembaca stage baru

`dashboard/data/` bukan berkas konfigurasi kecil: `evidence.json` ~250 MB, `personal_history.json` ~188 MB, `historical_timeline.json` ~57 MB (**dulu 570 MB** — sejak 2026-08-09 cuma entry terakhir tiap ticker yang menyimpan snapshot penuh; lihat Known Gaps). Kalau di-`json.load` semua, isinya mengembang jadi objek Python **~3,5×** ukuran berkasnya.

Karena itu backend **tidak lagi** memuat semuanya. Dua berkas terbesar masuk `_BIG_STAGES` di `backend/app.py` dan dibaca lewat **indeks offset byte** (`backend/big_json.py`): satu pemindaian mencatat posisi tiap record, lalu tiap pembacaan `seek` + parse satu potong (5-12 ms). Indeksnya ~0,4 MB. Terukur: backend **3,32 GB → 0,59 GB**, committed sistem 9,21 → 6,91 GB.

**Aturan yang harus diikuti kalau menambah endpoint/pembaca baru:**

1. **Jangan panggil `_get_stage("evidence")` atau `_get_stage("historical")`.** Pakai `_get_big_record()` untuk satu ticker, atau `_get_derived()` untuk ringkasan populasi. Keduanya punya jalur cadangan ke `json.load` lama, jadi salah pakai tidak merusak data — cuma diam-diam mengembalikan 2,7 GB.
2. **Pembaca yang cuma butuh SATU field adalah yang paling berbahaya**, justru karena kelihatan murah. `/api/consistency` sempat memanggil `_get_stage("evidence")` hanya untuk membaca `session_id`, dan itu mem-parse 250 MB penuh lalu menahannya — penghematannya batal tiap kali halaman dimuat. Sekarang lewat `big_json.read_session_id()` (8 KB pertama).
3. **Ukur memori SESUDAH membuka halaman, bukan sesudah warm cache.** Kebocoran di atas tidak terlihat sama sekali di angka startup.
4. **Endpoint yang mengirim seluruh populasi harus punya versi `/summary`** yang membuang array besarnya di server. `/api/evidence` dan `/api/historical` mentah sekarang **413** — bukan karena rusak, tapi karena tidak ada yang memakainya dan membiarkannya berarti menyediakan cara termudah menjatuhkan backend.
5. **Untuk skrip analisis sekali-pakai: jangan buka ribuan berkas cache satu-satu.** Pernah terukur >30 menit tanpa selesai untuk 5.265 berkas (dugaan: pemindaian AV per berkas), dan seluruh mesin ikut tercekik. Ambil sampel + cache hasil antara ke satu berkas kecil.

---

## Known Gaps (jujur, per 2026-08-08 — cek ulang sebelum percaya, ini snapshot bukan live status)

Supaya tidak ada yang menganggap sesuatu "pasti sudah dikerjakan" padahal belum:

- **Risk**: dari 7 `flag_id`, **5 punya jalur deteksi nyata** — dilution, auditor change & restatement (8-K item 4.01/4.02), fraud/delisting (item 1.03), delisting notice (item 3.01). Hard-gate `halted` **sudah benar-benar terpicu** di data nyata (5 ticker, run 2026-08-08). Yang masih `undetermined` di semua ticker: **litigation_material** (tidak ada item code 8-K untuk litigasi — hidupnya di teks "Legal Proceedings" 10-K/10-Q yang tidak diparse) dan **insider_selling_90d**. Keduanya ditandai `availability="no_source"` supaya tidak menghukum confidence secara konstan se-universe.
- **Reasoning**: bobot & kriteria 3 lensa masih placeholder spec ("didiskusikan terpisah, belum final") — jangan anggap angka skornya sudah dikalibrasi serius. Ini yang paling sering disalahpahami sebagai "sudah jadi".
- **Peer**: `peer_failures` selalu `[]` — parameternya sudah ada dan diteruskan ke semua perbandingan, tapi pipeline tidak pernah mengisinya (butuh Screening kirim daftar kandidat per-sektor). `roe_comparison` sebenarnya data institutional-ownership yang salah label (tidak ada field ROE asli).
- **Historical (publik)**: `outcome` **sengaja** `None` selamanya di v2.1 — itu keputusan, bukan pekerjaan yang tertinggal. Evaluasi nyata ada di lapisan pribadi.
- **`historical_timeline.json` — DUA BENTUK ENTRY** (sejak 2026-08-09, dulu di daftar ini sebagai pertumbuhan tanpa rem): entry **terakhir** tiap ticker menyimpan `aggregator_output` penuh, entry lebih tua cuma bentuk **tipis** (`{"thin":true,...}`: stance+skor per lensa, halted, konvergensi, `mv_reasoning`) ~44× lebih kecil; retensi 730 → **365** hari. Live: **570,7 MB → 57,0 MB**. Konsekuensi untuk penulis kode baru: **jangan asumsikan tiap entry punya `aggregator_output`** — baca lewat `readHistoricalEntry()` (frontend) atau cek bentuknya (backend). Proyeksi mantap ~433 MB pada 365 hari; kalau perlu lebih kecil, yang digeser **horizonnya** (180 hari ~236 MB, 90 ~141 MB), bukan isi entry tipisnya — ekornyalah yang dominan.
- **Confidence**: komponen "source consistency" dari spec belum ada. `recency_penalty` mati struktural (`evidence_age_days`=0 untuk semua, karena Evidence selalu "lahir" di run yang sama — umur cache tidak tercermin).
- **SEC Form 4**: lihat §SEC Form 4 di atas — cuma hitungan filing, bukan arah/volume transaksi. `insider_transactions` di Knowledge selalu kosong.
- **market_breadth** (Layer 1): butuh Screening pernah jalan sekali dulu (pakai cache harganya) — kalau belum, `status=missing`.
- **`json_safe.py`**: NaN/Infinity dari pandas/JSON perlu di-null-kan manual di titik serialisasi — kalau nambah tempat tulis JSON baru ke `dashboard/data/`, pastikan lewat `dumps_safe()`, bukan `json.dumps()` biasa (pernah 3x jadi bug nyata: Layer 1 SPX MA, Screening `last_price`).

### Satu kelas bug yang sudah muncul 4x — jangan jadi yang kelima

Pemeriksa ditulis melawan kata/satuan yang produsennya tidak pernah pakai, lalu diam-diam mati tanpa error:

| Pemeriksa mencari | Produsen sebenarnya menulis |
|---|---|
| substring `"miss"` | `"N/M beat"` |
| `"positive"` | `"accelerating"` |
| `net_margin > 0.05` | `5.0` (skala poin-persen) |
| `debt_to_equity` rasio | persen |

Efeknya bukan crash, tapi field yang selalu bernilai sama di seluruh universe. **Detektor yang paling produktif menemukan ini**: jalan rekursif ke tiap dotted path di berkas tahap, hitung persentase terisi dan jumlah nilai distinct. Path yang 100% kosong atau cuma 1 nilai distinct langsung menunjuk bug nyata. Jalankan itu sebelum percaya sebuah komponen "sudah bekerja".

---

## Testing

Tidak ada suite pytest formal kecuali `test_quarterly_trends.py`. Verifikasi tahap lain historisnya dilakukan manual: `ast.parse()` untuk cek syntax, script smoke-test sekali pakai per perubahan (biasanya ditulis ke scratch lalu dihapus setelah verifikasi), dan validasi end-to-end di browser lewat dashboard beneran. Kalau menambah logic baru yang penting, pertimbangkan nambah test nyata — jangan cuma ikut pola lama ini karena "sudah biasa begitu".

**`ast.parse()` tidak cukup, dan itu sudah terbukti mahal.** `build_calibration` pernah dipanggil tanpa pernah di-import → `NameError` tiap run. Pipeline tetap exit 0 karena blok pribadi dijaga `try/except` best-effort (rancangannya benar), jadi satu-satunya tanda cuma satu baris ERROR di log sementara `calibration.json` diam-diam tidak pernah dibangun ulang. **`try/except` best-effort menelan `NameError` sediam kegagalan data** — kalau menambah panggilan ke dalamnya, uji pengikatan namanya terpisah (pemeriksa AST atas semua nama yang dipanggil di blok itu + `exec_module` beneran).

Beberapa disiplin lain yang terbukti berharga di repo ini dan layak diulang:

- **Hitung acuan kebenarannya di Python DULU, sebelum melihat UI.** Fitur "umur tesis" nyaris tayang dengan semua streak ter-reset ke 1; yang menangkapnya adalah acuan independen yang dihitung sebelum membuka browser, bukan pemeriksaan visual.
- **Tulis prediksimu sebelum menjalankan pengukuran.** Saat mengukur base rate, prediksi "selisih tetap nol" ternyata salah — dan justru kesalahannya yang jadi temuan terbesar.
- **Hitung galat baku sebelum memvonis menang/kalah.** Versi pertama skrip pengukuran menyimpulkan "memilih lebih buruk daripada tidak memilih" hanya karena selisihnya negatif 2pp, tanpa galat sama sekali.
- **Kalau aturan diubah SESUDAH melihat hasil jelek, pastikan aturan barunya lebih SULIT, bukan lebih gampang.**
- **Jangan pindai JSON baris-per-baris** — kunci bersarang yang senama (`personal_call_set.speculative` vs `outcome.speculative`) akan diperlakukan sebagai satu. Berkas 167 MB ternyata cuma ~1 menit di-`json.load`; tetap parse.
- **Sebelum menyebut sesuatu "anomali satu record", hitung dulu** — lalu cari apa yang HILANG diam-diam gara-gara data rusak itu, bukan cuma apa yang terlihat salah. "1 record" pernah ternyata 450, dan kerugian sebenarnya adalah 43 vonis-z yang diam-diam jadi null.
- **Sebelum menyebut sebuah status "salah", ukur dulu apakah datanya memang menjangkau pertanyaannya.** Flag auditor hampir selalu `undetermined` bukan karena pemeriksanya rusak, tapi karena jendela filing cuma ~200 hari sementara pertanyaannya 3 tahun.

### Jebakan lingkungan (Windows, mahal, jangan diulang)

- **PowerShell 5.1 merusak berkas UTF-8**: `(Get-Content x -Raw).Replace(...) | Set-Content x -Encoding utf8` mendekode sebagai ANSI lalu menulis ulang sebagai UTF-8 (mojibake + BOM), dan `ast.parse` langsung gagal di U+FEFF. Untuk edit massal di berkas ber-non-ASCII, pakai skrip Python dengan `encoding="utf-8"` eksplisit.
- **Heredoc `<<'EOF'` bukan sintaks PowerShell.** Pesan commit: tulis ke berkas lalu `git commit -F`. Skrip Python: tulis berkas dulu, jangan pipe ke `python -`.
- **Nama berkas terlarang Windows**: ticker `CON` (simbol NYSE nyata) → `CON.json` ditolak OS. `cache.py` sudah menanganinya; ini pernah diam-diam merusak SETIAP percobaan full-market sebelumnya.
- **Jangan salurkan skrip panjang ke `| tail`** — pipa menahan seluruh cetakan sampai proses selesai, jadi progres tidak kelihatan dan ikut hilang kalau prosesnya dihentikan.
- **Ukur waktu muat halaman dari browser** (`performance.now()` + `fetch`), bukan `Invoke-WebRequest` — yang terakhir mengukur overhead-nya sendiri dan berayun 5-13 detik untuk permintaan yang sama.
- **Perubahan kode di tengah run tidak berlaku untuk proses yang sedang jalan.** Kalau perbaikannya harus ikut run itu, hentikan dan mulai ulang.
- **Berkas `.tmp` 0 byte bukan bukti proses mati** — bisa jadi penulisan ratusan MB yang belum ter-flush. Tunggu exit code sebelum menyatakan kegagalan.
- **`pythonw` di mesin ini adalah App Execution Alias**, jadi selalu ada **dua** proses `pythonw.exe`: shim ~5 MB plus interpreter asli sebagai anaknya. Itu normal — jangan "rapikan jadi satu".

---

© AlphaForge v2
