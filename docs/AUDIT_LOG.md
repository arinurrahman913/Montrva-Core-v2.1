# Catatan Audit AlphaForge v2.1

Riwayat audit menyeluruh terhadap basis kode, beserta status tiap temuan.
Tujuan berkas ini: temuan tidak hilang di percakapan, dan setiap audit
berikutnya punya titik awal yang jelas — termasuk daftar hal yang **sengaja**
belum dikerjakan, supaya tidak dilaporkan ulang sebagai "baru".

Status yang dipakai:

| Status | Arti |
|---|---|
| SELESAI | Sudah diperbaiki dan diverifikasi |
| TERBUKA | Nyata, belum dikerjakan |
| DITERIMA | Keterbatasan yang disadari, sengaja dibiarkan |
| GUGUR | Setelah ditelusuri, ternyata bukan masalah |

---

## Audit #2 — 2026-07-30 (setelah commit `ca6bf5b`)

Tiga agen paralel, semuanya read-only: (1) sumber data & concurrency,
(2) tahap analisis, (3) backend/pipeline/frontend. Fokus utamanya **memeriksa
perubahan `ca6bf5b` itu sendiri**, karena itu kode yang paling belum teruji.

Dua agen pertama secara independen menemukan regresi kritis yang sama.

### Regresi dari `ca6bf5b` — SELESAI

#### A1. Volatilitas membengkak ~2x untuk seluruh universe — SELESAI
`knowledge_helpers.py::calculate_volatility`

`_downsample_price_history` membuat `price_history` tidak lagi berjarak
seragam (~48 bar bulanan + 252 bar harian), tetapi `calculate_volatility`
menghitung selisih antar **semua** bar berurutan lalu melabelinya "return
harian". Sekitar 16% sampel jadi gerakan sebulan, yang deviasinya ~sqrt(21)
≈ 4.6x lebih besar.

Terukur pada 353 ticker cache nyata:

| | Nilai benar | Sebelum perbaikan |
|---|---|---|
| Rasio inflasi volatilitas | 1.00x | **1.98x** (maks 4.14x) |
| `risk.py` red flag `high_volatility` (>5.0) | 22% ticker | **59% ticker** |
| `reasoning.py` "High volatility" (>4.0) | 33% ticker | **75% ticker** |

Jadi lebih dari separuh universe mendapat red flag volatilitas palsu, dan
cabang "Low volatility" praktis tak terjangkau.

**Perbaikan**: hanya memakai pasangan bar yang jaraknya <= 7 hari kalender
(`_DAILY_GAP_MAX_DAYS`). Menyaring berdasarkan TANGGAL, bukan mengambil 252
bar terakhir, supaya tetap benar untuk histori harian penuh (jalur CLI, atau
kalau downsampling diubah) tanpa bergantung pada konstanta di modul lain.
Sesudah perbaikan rasio kembali 1.00x dan ambang cocok (22% vs 22%).

#### A2. Perbaikan `close=None` tidak dirambatkan ke fungsi tetangga — SELESAI
`knowledge_helpers.py::calculate_volatility`, `::calculate_high_low_52w`

`ca6bf5b` mengeraskan `calculate_returns` terhadap `close=None` (hasil NaN
yang di-null-kan `json_safe` saat serialisasi, lalu kembali sebagai
`PriceBar(close=None)`), tetapi `calculate_volatility` (`prev_close > 0`) dan
`calculate_high_low_52w` (`max(closes)`) masih meledak `TypeError`.

Karena `run_knowledge` menelan exception per-ticker, ticker terkait hilang
dari `knowledge.json` tanpa jejak. **Inilah sisa selisih 4065 evidence vs
4063 knowledge** pada run 2026-07-30 — bug yang sama yang commit itu klaim
sudah ditutup.

**Perbaikan**: helper `_is_finite_number()` dipakai ketiganya. Diverifikasi
dengan bar `close=None` di tengah dan dengan seluruh bar `None`.

#### A9. Heuristik "file besar" melewatkan justru berkas TERBESAR — SELESAI
`scripts/refresh_full_pipeline.py::_atomic_write`

`ca6bf5b` menulis file besar tanpa indent untuk menghemat memori, tapi
deteksinya hanya memindai **list** di level atas:

```python
big = any(isinstance(v, list) and len(v) > 500 for v in data.values())
```

`historical_timeline.json` berbentuk `{ticker: {...}}` — ribuan nilai **dict**,
nol list level atas — jadi `big` bernilai False dan berkas **469 MB** itu tetap
ditulis ber-indent. Terverifikasi langsung dari byte awal berkas: `evidence.json`
compact, `historical_timeline.json` ber-indent.

Ini bukan sekadar boros: berkas itu bertambah ~82 MB tiap run, jadi dalam
belasan run melewati 1.5 GB dan **MemoryError yang sama kembali** — hanya
pindah berkas, dan tetap di penulisan TERAKHIR, setelah 86 menit kerja.
Perbaikan yang dibuat untuk mencegah masalah itu justru menyisakan sekring
berdurasi ~2 minggu.

**Perbaikan**: `_is_big_payload()` memeriksa dict maupun list, di level atas
maupun satu tingkat di dalamnya, dan aman terhadap input non-dict. Sekaligus
`.tmp` kini dihapus kalau penulisan gagal di tengah — sebelumnya `MemoryError`
meninggalkan berkas sementara ratusan MB di volume yang sama, memakan ruang
yang justru dibutuhkan percobaan berikutnya.

#### A10. API key Finnhub bocor ke log plaintext dan bisa tampil di layar — SELESAI
`finnhub.py`, `_retry.py`, terekspos oleh `backend/app.py::_dump_failure_log`

API key dikirim sebagai query string (`token=...`). `requests` memformat
`HTTPError` sebagai "401 Client Error: Unauthorized for url: <URL LENGKAP>" —
lengkap dengan token. Teks itu dicetak apa adanya ke stderr.

Sejak `ca6bf5b` menyimpan stderr penuh saat gagal, kredensial hidup itu (a)
tertulis plaintext ke `logs/refresh_failure_*.log`, dan (b) bisa terpilih oleh
`_summarize_failure` lalu disajikan `/api/refresh/status` — endpoint **tanpa
autentikasi** pada server yang mendengarkan di `0.0.0.0` — dan dirender ke DOM.
Kalau key kedaluwarsa, ini terjadi 4065 kali dalam satu run.

**Perbaikan**: `_redact()` di finnhub.py, plus jaring pengaman umum di
`_retry.py` (`token=`/`api_key=`/`apikey=`/`key=`) supaya berlaku untuk semua
pemanggil, bukan hanya modul yang ingat menyaring sendiri.

#### A11. `_summarize_failure` membaca aliran yang salah — SELESAI
`backend/app.py`

Fungsi diagnosis yang ditambahkan `ca6bf5b` **tidak bekerja untuk hampir semua
kegagalan**. `refresh_full_pipeline.py` memasang
`logging.StreamHandler(sys.stdout)` dan melaporkan lewat `log.exception(...)`,
jadi traceback untuk kegagalan di Screening/Evidence/Knowledge/dst mendarat di
**stdout**, bukan stderr. Sementara stderr justru penuh baris progres
("Peer Comparison complete: 4055 results") yang tidak cocok pola noise mana pun.

Akibatnya spanduk dashboard akan berbunyi:
`✕ Gagal: Peer Comparison complete: 4055 results` — pesan SUKSES disajikan
sebagai sebab kegagalan. Persis kelas masalah "2 jam hilang tanpa petunjuk"
yang fungsi itu dibuat untuk mencegahnya.

**Perbaikan**: memeriksa stdout dan stderr; mencari baris exception sungguhan
lewat pola `NamaError: ...` lebih dulu, baru jatuh ke pemindaian baris bermakna;
dan memotong pesan di 400 karakter (exception pandas/numpy bisa membawa repr
array raksasa ke field JSON yang di-poll tiap 2,5 detik).

#### A12. Jalur timeout tidak menyimpan apa pun & salah menyebut durasi — SELESAI
`backend/app.py`

Timeout adalah kegagalan **termahal** yang bisa terjadi (6 jam kerja), dan
justru satu-satunya jalur yang tidak memanggil `_dump_failure_log` —
`TimeoutExpired` membawa stdout/stderr yang sudah terkumpul, dan itu dibuang.
Selain itu `1800 // 3600 == 0`, jadi run per-sektor melaporkan
"Timeout (>0 jam)" — commit yang katanya memperbaiki durasi menyesatkan justru
mengganti satu angka salah dengan angka salah lain.

**Perbaikan**: log lengkap ikut disimpan pada timeout, dan durasi dinyatakan
dalam menit bila di bawah sejam.

### Regresi dari `ca6bf5b` — TERBUKA

#### A3. `Ctrl+C` saat Evidence menggantung sampai run selesai (~70 menit) — TERBUKA
`evidence.py::run_evidence`

Seluruh ~4065 task disubmit di muka, dan `with ThreadPoolExecutor(...)` saat
keluar memanggil `shutdown(wait=True)` tanpa `cancel_futures=True`. Menekan
Ctrl+C di ticker ke-200 tetap menguras 3865 task sisanya sebelum interupsi
sempat merambat. Versi serial yang digantikan berhenti seketika.

Perbaikan yang disarankan: `executor.shutdown(cancel_futures=True)` di
`finally`, atau submit bertahap.

#### A4. Throttle untuk 3 endpoint Yahoo ternyata placebo — TERBUKA
`yahoo_evidence.py::_apply_batch_delay`

`ca6bf5b` menambahkan `_apply_batch_delay()` ke `_fetch_institutional_holders_detail`,
`_fetch_earnings_history`, dan `_fetch_revenue_estimate`. Tapi fungsi itu
sendiri hanya tidur bila kurang dari 2 detik berlalu sejak batas batch
sebelumnya — persis pola "jeda periodik yang keburu terlampaui" yang
docstring `finnhub.py` sendiri bilang sudah didiagnosis dan diganti dengan
min-interval.

Dengan angka run nyata (1.06 dtk/ticker, ~5 panggilan Yahoo/ticker), 20
panggilan memakan ~4.2 detik — selalu > 2.0, jadi `time.sleep` praktis tidak
pernah dieksekusi. Ketiga endpoint itu masih dihantam tanpa jeda; gejala yang
mau diobati belum teratasi.

Catatan yang sudah diperiksa dan **aman**: `_apply_batch_delay` tidur sambil
memegang lock itu justru konstruksi yang benar untuk throttle global —
melepas lock sebelum tidur akan membuat 5 thread menghitung target yang sama
lalu menyerbu bersamaan. Tidak ada reentrancy dan tidak ada lock yang ditahan
melintasi panggilan `retry()`.

#### A13. Chart `price_history` diplot per-indeks, dan dilabeli "Tren 1 Tahun" — TERBUKA
`frontend/src/format.js` (`sparklinePoints`), `TickerModal.jsx`, `ThesisProof.jsx`

Sumbu X murni ordinal (`x = (i / (sample.length - 1)) * width`); tidak ada
plotting berbasis tanggal di mana pun di frontend. Dengan 301 bar (indeks 0-48
= **4 tahun** bulanan, indeks 49-300 = **1 tahun** harian), 4 tahun pertama
menempati ~16% lebar chart sementara satu tahun terakhir menempati 84% —
dan judulnya masih **"Tren 1 Tahun"**, dengan normalisasi lo/hi melintasi
5 tahun penuh.

Contoh dampak: saham yang naik $2 -> $200 dalam lima tahun lalu datar di ~$195
sepanjang tahun ini akan menampilkan lonjakan hampir vertikal di 16% kiri
(kejadian empat tahun lalu) di bawah judul "Tren 1 Tahun".

`ThesisProof.jsx` aman untuk saat ini karena menyaring `b.date >= since` dan
belum ada tesis yang lebih tua dari setahun — akan terdistorsi begitu ada.
(Laten di baris yang sama: `since` berupa timestamp ISO penuh sementara
`b.date` hanya `YYYY-MM-DD`, jadi perbandingan string menjatuhkan bar hari
anchor itu sendiri.)

Terkait: `contracts.py` masih mendokumentasikan `price_history` sebagai
"1-year daily OHLCV" — asumsi yang sama yang tertanam di UI.

#### A5. `dump_safe` hanya menghapus separuh lonjakan memori — TERBUKA
`json_safe.py::dump_safe`

Docstring mengklaim tidak ada struktur raksasa yang ditahan, tapi
`json.dump(_sanitize(obj), fp)` **memateralisasi `_sanitize(obj)` sepenuhnya
lebih dulu** — satu salinan penuh struktur — sebelum satu byte pun ditulis.
Jadi 2 dari 4 salinan hilang, bukan 3. Kalau universe tumbuh atau downsampling
dimatikan, `MemoryError` yang sama kembali di baris yang sama.

Perbaikan sebenarnya: subclass `json.JSONEncoder` supaya sanitasi terjadi
per-nilai saat emisi.

#### A6. Anchor return_3y/return_5y turun ke granularitas akhir-bulan — DITERIMA
`yahoo_evidence.py::_downsample_price_history` x `knowledge_helpers::_find_price_on_or_before`

Anchor 3 dan 5 tahun selalu jatuh di wilayah bulanan, jadi bar terpilih bisa
meleset sampai ~15 hari dari tanggal target (versus ~1-3 hari dengan bar
harian). Klaim "nilai identik persis" di pesan commit `ca6bf5b` hanya berlaku
untuk 8 ticker sampel yang kebetulan anchor-nya dekat akhir bulan; secara umum
bisa bergeser ~1-3 poin persen.

Diterima: ini konsekuensi langsung dari keputusan desain menyimpan bar bulanan
untuk tahun ke-2..5 (disetujui pengguna, alternatifnya evidence.json 1.3GB dan
OOM). Toleransi 45 hari mencegah metriknya HILANG, sekadar tidak persis.

Terkait: `_find_price_on_or_before` menerima bar SESUDAH target juga, dan
karena downsampling menyimpan hari bursa TERAKHIR tiap bulan, anchor selalu
condong ke sisi yang lebih baru — bias searah untuk seluruh universe,
memperkecil `return_5y` sekitar 1-2% relatif.

### Regresi dari `ca6bf5b` — perlu keputusan

#### A7. Ambang band Confidence tidak dikalibrasi ulang setelah cek dihapus — TERBUKA
`confidence.py` (penghapusan cek) vs ambang `>= 70 high` / `>= 40 medium`

Menghapus cek untuk field yang tidak pernah terisi menaikkan *denominator*
empat section berbobot gabungan 50%. Maksimum yang bisa dicapai naik dari
**81.4 ke 100** — ticker di plafon lama naik sekitar **18.6 poin**, sementara
ambang band tidak disentuh.

Konsekuensi yang perlu dipertimbangkan:
- `personal/personal_reasoning.py` mendokumentasikan bahwa audit 2026-07-29
  menemukan `band=='high'` **tidak pernah muncul** di seluruh universe —
  temuan itu kini gugur diam-diam.
- `confidence.py` dan `reasoning.py` sama-sama **mengosongkan `limiters`
  saat `band == "high"`**, jadi ticker yang kini naik ke "high" berhenti
  menerbitkan catatan keterbatasan datanya.
- Gerbang keamanan di `personal/personal_contracts.py` (`band == "low"`
  memblokir aksi eksposur penuh) berhenti memicu untuk ticker yang terdorong
  dari 35 ke 50.

Ini keputusan kalibrasi, bukan bug yang jelas — perlu dibahas: apakah ambang
70/40 dinaikkan agar makna "high/medium/low" tetap sama seperti dulu, atau
memang diterima bahwa sekarang lebih banyak ticker layak "high".

#### A8. `_score_competitive_momentum` jadi degenerate (0% atau 100%) — TERBUKA
`confidence.py::_score_competitive_momentum`

Tersisa satu cek, jadi skornya biner dan mengayunkan `overall.score` sebesar
5 poin penuh hanya berdasarkan `acceleration_signal` — yang sendirinya butuh
>=5 kuartal data SEC EDGAR, jadi sebenarnya proksi untuk *cakupan EDGAR*,
bukan kualitas data momentum. Saat 0, limiter "competitive_momentum data
incomplete (0/1)" terbit untuk setiap ticker non-high, terbaca seperti data
hilang padahal artinya "perusahaan ini punya kurang dari 5 kuartal laporan".

### Temuan lama yang belum dikerjakan (dikonfirmasi masih ada)

#### B1. Peta CIK gagal sekali -> ~16.000 percobaan jaringan — TERBUKA
`sec_parser.py::_get_ticker_cik_map`

Memo yang ditambahkan `ca6bf5b` hanya menutup jalur sukses; blok `except`
mengembalikan `{}` tanpa memo dan tanpa negative-cache. Kalau `sec.gov`
membalas 503 di panggilan pertama, setiap `get_cik_from_ticker` — 4x per
ticker x 4065 ticker = ~16.260 panggilan — masuk jalur jaringan lagi, masing-
masing membayar rate limit + 2 percobaan + backoff 3 detik. Minimal ~13,5 jam
tambahan; run tampak menggantung, bukan gagal.

#### B2. Error non-RequestException di parsing news membuang seluruh paket ticker — TERBUKA
`finnhub.py::fetch_company_news`

Blok parsing hanya dijaga `except requests.exceptions.RequestException`.
Kalau Finnhub membalas HTTP 200 dengan objek JSON (bukan list), atau ada item
dengan `"datetime": null`, yang terlempar `AttributeError`/`TypeError` —
lolos sampai `evidence.py`, dan `price_market`, `fundamental`, filing SEC yang
sudah berhasil diambil **ikut dibuang** gara-gara berita.

#### B3. Tidak ada circuit breaker saat Finnhub 403 — TERBUKA
Kalau paket Finnhub diturunkan sehingga `company-news` jadi premium, setiap
ticker tetap membayar 1.05 detik rate limit SEBELUM tahu kena 403, dan jalur
403 (benar) tidak menyimpan cache — jadi ~71 menit terbakar tiap run tanpa
menghasilkan apa pun, berulang selamanya.

#### B4. Cache per-CIK jadi tulis-bersamaan untuk ticker dwi-kelas — TERBUKA
Tiga namespace cache (`facts_{cik}`, `submissions_{cik}`, `form4_activity_{cik}`)
dikunci pada CIK, bukan ticker. `screening_result.passed` urut abjad, jadi
GOOG/GOOGL, FOX/FOXA, HEI/HEI-A berdekatan dan hampir pasti berada dalam
jendela 5 worker yang sama -> dua `write_text` bersamaan ke file yang sama.
Self-healing (pembaca dapat parse error -> dianggap cache miss), tapi boros.
Tidak ada saat Evidence masih serial.

#### B5. Falsy-check lain yang 0.0-nya sah — TERBUKA
`ca6bf5b` memperbaiki 3 tempat di `knowledge.py`. Yang tersisa:
- `knowledge.py::_fcf_margin_pct` — FCF tepat 0.0 (breakeven) -> `None`
- `knowledge_helpers.py` `fcf_yield` — sama; ini merambat: dihitung hilang oleh
  `_score_valuation` DAN masuk `missing_fields`, dan pada >=3 gap stance
  Multibagger berbalik ke `ruang_tak_terbaca`
- `knowledge_helpers.py` `revenue_yoy_q4` — perusahaan yang pendapatannya jatuh
  **ke nol** kehilangan sinyalnya; itu justru sinyal fundamental paling
  mengkhawatirkan yang bisa muncul
- `capital_expenditures == 0` normal untuk perusahaan software asset-light,
  tapi `capex_pct_revenue_q4` jadi hilang untuk justru kelompok itu

#### B6. Kegagalan Yahoo sesaat memalsukan katalis "cancelled" — TERBUKA
`catalyst_history.py`

Loop yang meresolusi katalis yang hilang tidak menjaga `cs.status == "missing"`.
Kalau `_fetch_yahoo_info` gagal sekali, `CatalystSet` kosong -> tanggal earnings
yang masih di masa depan ditulis sebagai `lifecycle_status="cancelled"`.
Pembatalan palsu itu permanen dan tampil di UI; katalis aslinya muncul lagi
esok hari sebagai entri "scheduled" baru tanpa kesinambungan.

#### B7. CLI `knowledge` mati total — TERBUKA
`cli.py` — `EvidencePackage(...)` dibangun tanpa `institutional_activity`, yang
wajib dan tanpa default -> `TypeError` di paket pertama. Jalur produksi
(`scripts/refresh_full_pipeline.py`) memanggil `run_knowledge` langsung, jadi
ini tidak pernah ketahuan. CLI juga menjatuhkan `company_profile`,
`analyst_estimates`, `insider_percentage`, `top_holders`, `roe`/`roa`, dan
memanggil reasoning tanpa peer/catalyst/Layer 1 — sehingga skor dari CLI tidak
sebanding dengan skor produksi.

#### B8. Dua nilai `fast_info` lolos koersi tipe — TERBUKA
`yahoo_evidence.py` — `market_cap` dan `shares_outstanding` disimpan mentah,
tidak seperti field OHLCV di bawahnya yang di-cast eksplisit. `json_safe`
hanya mengenali subclass `float`, jadi `numpy.int64` dari `fast_info` akan
melempar `TypeError` di dalam `cache_set`, ditangkap `except` lebar, dan
membuang fetch harga yang **sebenarnya sukses** sebagai `status="missing"`.
Belum terjadi (yfinance mengembalikan skalar Python di sini), tapi hanya
berjarak satu kenaikan versi dependensi.

#### B9. `.info` kosong di-cache dan dilaporkan `status="ok"` — TERBUKA
Ticker delisted yang membuat yfinance mengembalikan `{}` menuliskan `{}` itu
ke cache `yahoo_info` dan disajikan 24 jam; `fetch_fundamental_data`
menetapkan `status="ok"` tanpa syarat, jadi semua field `None` sementara
metadata mengklaim fetch berhasil.

#### B10. Komentar kontrak bertentangan dengan skala sebenarnya — TERBUKA
`knowledge_contracts.py` mendokumentasikan `institutional_pct`/`insider_pct`
sebagai "(0-100)", padahal nilainya pecahan 0-1 dari Yahoo. Konsumen saat ini
(`reasoning.py`) memperlakukannya sebagai pecahan sehingga perilakunya benar,
tapi konsumen berikutnya yang percaya docstring akan meleset 100x.

#### B11. `personal_evaluation.py` ikut terdampak downsampling — TERBUKA
`_reconstruct_start_price` mengambil `bars[0]` dari semua bar sejak
`since_date`. Untuk call berumur 18 bulan, sekarang mengembalikan harga akhir
bulan yang meleset sampai 30 hari dari tanggal call sebenarnya, dan hasilnya
memberi makan klasifikasi `terbukti`/`meleset`.

### Diperiksa dan bersih

- **Cache tidak terdegradasi lintas run** — `asdict(result)` diambil SEBELUM
  `result.price_history` diganti versi ringkas, dan jalur cache-hit tidak
  pernah menulis. Tidak ada jalur yang menulis balik data ringkas ke cache.
- **Urutan lock / deadlock** — `_lock` dan `_cik_map_lock` tidak pernah
  ditahan bersamaan; tidak ada lock yang ditahan melintasi panggilan jaringan;
  tidak ada reentrancy.
- **Urutan hasil `run_evidence`** — `results[idx] = future.result()` benar-benar
  mempertahankan urutan `screening_result.passed`.
- **Cache news "degraded" TIDAK menyembunyikan gangguan** — ditelusuri tiap
  jalur gagal: 403 kembali sebelum `cache_set`, 401/5xx melempar lewat
  `raise_for_status`, 429 melempar setelah retry, API key hilang kembali lebih
  awal. Hanya HTTP 200 dengan `[]` yang di-cache — itu kasus small-cap yang sah.
- **Batas bulan `_downsample_price_history`** — `sorted()` atas kunci "YYYY-MM"
  kronologis; last-write-wins menghasilkan hari bursa terakhir tiap bulan;
  urutan keluaran tetap monoton; histori pendek/kosong ditangani.
- **Satuan `net_margin_trend.q4`** — diperiksa di semua konsumen, semuanya
  persen-poin. Perbaikan format `:.2f}%` benar.
- **Arah `revenue_growth_comparison`** — benar ujung ke ujung.
- **Layer 1 sources** (`fred.py`, `sentiment.py`, `yahoo.py`) — bersih.

---

## Audit #1 — 2026-07-30 (sebelum commit `ca6bf5b`)

Tiga agen paralel dengan pembagian yang sama. Temuan yang sudah ditindak
masuk ke `ca6bf5b`; selebihnya berlanjut ke daftar TERBUKA di Audit #2.

### SELESAI di `ca6bf5b`

| Temuan | Ringkas |
|---|---|
| `risk.py` margin -1230% | `net_margin_trend.q4` berskala persen-poin tapi diformat `:.2%` yang mengali 100 lagi |
| `confidence.py` 4 section mentok | 7 field yang tak pernah terisi dihitung sebagai "data hilang" -> penalti konstan; plafon 71%/60%/33%/67% |
| `peer.py` sinyal growth mati | `revenue_growth_comparison` dideklarasikan & dibaca reasoning, tapi tak pernah diisi -> bonus +6 tak pernah menyala |
| `knowledge.py` 0.0% dicap hilang | falsy-check pada institutional/revenue/FCF |
| 3 endpoint Yahoo tanpa throttle | (perbaikannya ternyata placebo — lihat A4) |
| Peta CIK dibaca ulang ~16.000x | memo in-process ditambahkan |

### Temuan struktural yang masih TERBUKA

#### C1. `/api/ticker/<t>` bisa mencampur data dari dua run berbeda — TERBUKA
`price_target.py::sync_price_target_history` dan
`catalyst_history.py::sync_catalyst_history` menulis ke disk **di tengah run**,
sebelum gerbang all-or-nothing, sementara 10 file lain menunggu gerbang itu.
`backend/app.py::get_ticker_detail` menggabungkan keduanya tanpa `generated_at`
atau `session_id` apa pun di respons, jadi tidak ada cara — bahkan secara
prinsip — bagi frontend atau pengguna untuk mendeteksi ketidaksesuaian.

Ini terbukti aktif saat audit #1: `price_target_history.json` punya snapshot
2026-07-30 sementara `evidence.json` masih dari 2026-07-29.

Tidak aktif selama data konsisten satu run, tapi muncul lagi setiap kali ada
run yang gagal di tengah.

#### C2. Blok tulis "gerbang" tetap 10 tulis file terpisah — TERBUKA
Masing-masing atomik sendiri (tmp + `os.replace`), tapi tanpa transaksi lintas
file. Kill eksternal di antara dua tulis menghasilkan mis. rekomendasi
Aggregator yang merujuk penilaian Risk dari hari berbeda.

#### C3. `_get_stage` menahan seluruh file JSON di memori selamanya — TERBUKA
`backend/app.py` — `json.load` seluruh berkas lalu disimpan permanen di
`_stage_cache`. Flask teramati ~2GB RSS di era evidence.json 340MB.
`historical_timeline.json` kini 469MB dan bertambah tiap run, dan tidak ada
endpoint ringan `/api/historical/summary` (padahal `/api/evidence/summary` ada).

#### C4. `_retry.py` bisa kehabisan thread — TERBUKA
Satu pool global 32 worker dipakai semua modul. Panggilan yfinance yang
menggantung (pernah terjadi: 40+ menit tanpa exception) membuat thread-nya
hilang permanen karena Python tidak bisa membunuh thread. Kerugian menumpuk
sepanjang run panjang, dan `atexit` join bisa membuat proses tidak pernah
keluar.

#### C5. ETA di tombol Generate — GUGUR
`GenerateButton.jsx` menulis "~1.5-2 jam"; run terverifikasi memakan **86 menit
(1,43 jam)**, jadi ETA-nya sekarang justru sedikit konservatif, bukan optimistis
— ditutup oleh concurrency 5-thread dan downsampling. Catatan: run dengan cache
dingin masih bisa melewati batas atas 2 jam.

### Temuan tambahan dari Audit #2 (di luar `ca6bf5b`)

#### C6. `/api/historical` mengirim 469 MB dalam satu respons — TERBUKA
`HistoricalView.jsx` memanggil `api.historical()` -> `GET /api/historical` ->
`jsonify(_get_stage("historical"))`, lalu `_compress_response` menjalankan
`gzip.compress(response.get_data())` — seluruh body dimaterialisasi sebagai
string, lalu sebagai bytes, lalu buffer gzip, sekaligus, single-threaded,
memegang GIL.

Padahal view itu hanya memakai lima skalar per ticker (`ticker`,
`total_entries`, `last_entry_date`, `outcome`, `halted`). Satu klik nav bisa
membuat Flask (sudah ~2 GB RSS) langsung MemoryError atau macet bermenit-menit,
selama itu semua request lain — termasuk `/api/refresh/status` yang di-poll
tiap 2,5 detik — ikut menggantung.

Terkait C3: total 13 berkas stage kini ~866 MB di disk, dan `_warm_cache`
sengaja memuat **semuanya** saat startup. Dalam ~2 minggu run harian,
`_warm_cache` sendiri akan gagal.

#### C7. `POST /api/refresh/<mode>` tanpa autentikasi di `0.0.0.0` — TERBUKA
Siapa pun di jaringan yang sama bisa memulai pipeline 6 jam, atau membaca
`/api/refresh/status`. `personal_routes.py` menyatakan asumsi "hanya lokal",
tapi `app.run(host="0.0.0.0")` bertentangan dengan itu.

#### C8. `save_personal_history` menulis non-atomik — TERBUKA
`personal/personal_historical.py` memakai `open(..., "w")` biasa — satu-satunya
penulis di basis kode yang tidak memakai tmp+replace. Kill saat menulis
meninggalkan JSON terpotong, dan karena berkas itu tidak ikut `_warm_cache`,
kegagalannya baru muncul sebagai HTTP 500 di `/api/personal/*` dan tidak pulih
sendiri.

#### C9. `18` tulis berurutan, bukan 10 — TERBUKA (memperjelas C2)
Rentang penulisan terukur 86 detik pada run 2026-07-30 (06:55:12 -> 06:56:38):
10 berkas stage + 2 personal + 3 layer1/source-health + 4 snapshot root.
Masing-masing atomik sendiri; tidak ada yang membuat himpunannya atomik.

#### C10. `catalyst_history.json` merusak diri sendiri saat run gagal — TERBUKA
Koreksi atas C1: berkas ini **tidak** disajikan backend sama sekali (tidak ada
di `STAGE_FILES`), jadi tidak bisa mencemari respons API. Kerusakannya justru
internal dan lebih halus: `sync_catalyst_history` membandingkan katalis hari ini
dengan state tersimpan untuk menurunkan status delayed/completed/cancelled. Run
yang gagal tetap memajukan state itu tanpa menghasilkan `catalysts.json` yang
sepadan — sehingga run sukses berikutnya membandingkan dengan state yang sudah
"terpakai", dan transisi katalis satu hari **hilang tanpa bisa dideteksi dari
luar**.

Catatan lain atas C1: `session_id` sebenarnya **sudah** ada di setiap elemen
`recommendations` dan ikut dikembalikan `/api/ticker/<t>`, dan setiap snapshot
price-target punya `date` sendiri. Jadi API secara teknis sudah memancarkan
cukup informasi untuk mendeteksi percampuran — **tidak ada satu konsumen pun
yang memeriksanya**.

---

## Catatan untuk audit berikutnya

Hal-hal yang **sudah** diperiksa dan tidak perlu dilaporkan ulang kecuali
kodenya berubah: daftar "Diperiksa dan bersih" di Audit #2, serta seluruh
keterbatasan berstatus DITERIMA dan GUGUR.

Pelajaran metodologis dari Audit #2: dua dari tiga agen secara independen
menemukan regresi volatilitas yang sama, dan ketiganya menemukan cacat di
perbaikan yang baru dibuat beberapa jam sebelumnya. **Setiap perbaikan besar
sebaiknya diaudit ulang sebelum dianggap selesai** — terutama yang mengubah
bentuk data (seperti downsampling), karena konsumennya tersebar dan asumsinya
sering implisit (jarak bar seragam, jumlah bar sebagai proksi waktu, sumbu X
ordinal).

Yang paling berdampak kalau mau dikerjakan berikutnya, berurutan:
1. **A13** — chart 5 tahun dilabeli "Tren 1 Tahun"; ini yang langsung salah di
   mata pengguna sejak sekarang
2. **C6 + C3** — satu klik nav dari OOM, dan makin dekat tiap run
3. **C1/C10** — kegagalan run merusak data yang sudah benar, tanpa jejak
4. **A7** — kalibrasi band Confidence; butuh keputusan, bukan sekadar perbaikan
5. **B2/B6** — kegagalan sesaat membuang kerja yang sudah berhasil
6. **A3** — Ctrl+C harus benar-benar berhenti
