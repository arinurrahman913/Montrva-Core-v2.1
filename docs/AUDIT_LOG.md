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

#### A13. Chart `price_history` diplot per-indeks, dan dilabeli "Tren 1 Tahun" — SELESAI
`frontend/src/format.js` (`sparklinePoints`), `TickerModal.jsx`, `ThesisProof.jsx`

Sumbu X murni ordinal (`x = (i / (sample.length - 1)) * width`); tidak ada
plotting berbasis tanggal di mana pun di frontend. Dengan 301 bar (indeks 0-48
= 4 tahun bulanan, indeks 49-300 = 1 tahun harian), 4 tahun pertama menempati
~16% lebar chart sementara satu tahun terakhir menempati 84% — dan judulnya
masih "Tren 1 Tahun", dengan normalisasi lo/hi melintasi 5 tahun penuh.

**Perbaikan**: `sparklinePoints` kini memposisikan X berdasarkan `bar.date`
(proporsional terhadap rentang waktu), dengan fallback ke index-based kalau
ada bar tanpa tanggal valid (mis. titik quote live yang disuntik terpisah).
`TickerModal.jsx` memakai `trendSpanLabel()` baru yang menghitung label dari
rentang tanggal sebenarnya, bukan string statis. `ThesisProof.jsx`: titik
quote live kini dikasih tanggal hari ini supaya ikut positioning date-based;
sekalian diperbaiki `b.date >= since` yang membandingkan `"YYYY-MM-DD"`
dengan timestamp ISO penuh dan leksikografis menjatuhkan bar hari anchor itu
sendiri. `contracts.py` diperbarui dari komentar "1-year daily OHLCV" yang
sudah tidak akurat.

Diverifikasi: `vite build` sukses, `oxlint` tidak menambah temuan baru pada
file yang diubah (dibandingkan sebelum perubahan).

### Regresi dari `ca6bf5b` — TERBUKA

#### A3. `Ctrl+C` saat Evidence menggantung sampai run selesai (~70 menit) — SELESAI
`evidence.py::run_evidence`

Seluruh ~4065 task disubmit di muka, dan `with ThreadPoolExecutor(...)` saat
keluar memanggil `shutdown(wait=True)` tanpa `cancel_futures=True`. Menekan
Ctrl+C di ticker ke-200 tetap menguras 3865 task sisanya sebelum interupsi
sempat merambat. Versi serial yang digantikan berhenti seketika.

**Perbaikan**: ganti `with ThreadPoolExecutor(...) as executor:` (tidak ada
cara mengoper `cancel_futures` lewat protokol with-statement) ke try/finally
manual, panggil `executor.shutdown(cancel_futures=True)` di `finally` — task
yang belum mulai (mayoritas) langsung dibatalkan, cuma menunggu
<=`EVIDENCE_WORKERS` task yang sudah mid-flight.

Diverifikasi kontras langsung kode lama vs baru dengan KeyboardInterrupt
disimulasikan setelah task ke-2 selesai (200 fake candidate, tiap task tidur
0.3s): kode lama 12.03s dengan 200/200 task tetap jalan semua; kode baru
0.60s dengan cuma 7/200 task yang sempat jalan.

#### A4. Throttle untuk 3 endpoint Yahoo ternyata placebo — SELESAI
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

**Perbaikan**: checkpoint-per-batch diganti min-interval PER PANGGILAN (pola
sama seperti `finnhub.py::_apply_rate_limit`) — `YF_EVIDENCE_MIN_INTERVAL_SECONDS
= YF_EVIDENCE_BATCH_DELAY_SECONDS / YF_EVIDENCE_BATCH_SIZE` (0.1 detik).
Diverifikasi kontras: kode lama pada 60 panggilan berjarak realistis (~0.21s)
cuma sleep SEKALI (checkpoint pertama, bug bootstrap terpisah), nol sleep di
checkpoint berikutnya — placebo total di steady state, persis diagnosis
audit. Kode baru menahan 20 panggilan rapid-fire ~1.9 detik (throttle aktif),
tidak menambah jeda untuk panggilan yang sudah lebih lambat dari interval
minimal (tidak over-throttle).

#### A5. `dump_safe` hanya menghapus separuh lonjakan memori — SELESAI
`json_safe.py::dump_safe`

Docstring mengklaim tidak ada struktur raksasa yang ditahan, tapi
`json.dump(_sanitize(obj), fp)` **memateralisasi `_sanitize(obj)` sepenuhnya
lebih dulu** — satu salinan penuh struktur — sebelum satu byte pun ditulis.
Jadi 2 dari 4 salinan hilang, bukan 3. Kalau universe tumbuh atau downsampling
dimatikan, `MemoryError` yang sama kembali di baris yang sama.

**Perbaikan**: `_dump_streaming` baru merekursi turun ke container
(dict/list) menulis delimiter langsung ke file handle, sanitasi NaN/inf
PER-LEAF saat emisi — tidak ada titik yang menahan salinan penuh/sebagian
besar struktur. Dipakai `dump_safe` hanya saat `indent is None` (satu-
satunya pemanggil produksi memakainya justru untuk payload besar); payload
ber-indent (selalu kecil by construction) tetap lewat jalur lama. Diverifikasi
12 kasus uji korektnes (termasuk key non-string, NaN/Infinity, unicode) —
semua cocok dengan baseline lama — plus `tracemalloc` pada struktur ~5000
item bergaya evidence.json: peak memory turun dari 35.33MB ke 0.33MB (99.1%).

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

#### A7. Ambang band Confidence tidak dikalibrasi ulang setelah cek dihapus — SELESAI
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

**Keputusan pengguna**: naikkan ambang secara proporsional supaya makna
"high/medium/low" tetap mirip seperti sebelum penghapusan cek.

**Perbaikan**: `BAND_HIGH_THRESHOLD = 86.0`, `BAND_MEDIUM_THRESHOLD = 49.0`
(dari rasio 100/81.381 = 1.2288 dikali 70/40) jadi konstanta modul di
`confidence.py`, menggantikan literal `70`/`40` di `assess_confidence` DAN 3
tempat lain di `reasoning.py` yang punya band-logic duplikat atau
membandingkan `confidence.overall.score` mentah terhadap ambang lama
(`_module_confidence`, `run_quality_lens`, `run_multibagger_lens`).
`personal_contracts.py` tidak perlu diubah — gatenya membaca `.band`
kategorikal, otomatis ikut threshold baru.

Diverifikasi: simulasi `KnowledgeProfile` terisi penuh (semua field non-dead
terisi) menghasilkan `base_score` persis 100.0, mengonfirmasi derivasi rasio
lewat kode sungguhan, bukan cuma aljabar di atas kertas.

**Catatan ketepatan** (dicatat juga di kode): rescale linear ini eksak untuk
ticker dengan kelengkapan data proporsional sama di semua section, tapi
TIDAK bijektif murni dari skor gabungan untuk ticker dengan pola kelengkapan
timpang antar section — tidak ada snapshot `knowledge.json` produksi di
lingkungan pengembangan untuk validasi percentile-exact. Ambang ini tetap
"kalibrasi awal" (sama seperti bobot/ambang lain di modul) — validasi ulang
terhadap distribusi skor produksi nyata begitu tersedia.

#### A8. `_score_competitive_momentum` jadi degenerate (0% atau 100%) — SELESAI (sebagian)
`confidence.py::_score_competitive_momentum`

Tersisa satu cek, jadi skornya biner dan mengayunkan `overall.score` sebesar
5 poin penuh hanya berdasarkan `acceleration_signal` — yang sendirinya butuh
>=5 kuartal data SEC EDGAR, jadi sebenarnya proksi untuk *cakupan EDGAR*,
bukan kualitas data momentum. Saat 0, limiter "competitive_momentum data
incomplete (0/1)" terbit untuk setiap ticker non-high, terbaca seperti data
hilang padahal artinya "perusahaan ini punya kurang dari 5 kuartal laporan".

**Perbaikan (scope terbatas, ketepatan pesan saja)**: limiter untuk
competitive_momentum sekarang menjelaskan penyebab sebenarnya ("revenue YoY
quarter-over-quarter tidak tersedia, perlu histori SEC EDGAR untuk 2 kuartal
berurutan"), bukan template generik "data incomplete (N/M)".

**Belum disentuh** (butuh keputusan kalibrasi seperti A7, bukan bug murni):
apakah section 1-cek biner pantas tetap mengayunkan `overall.score` 5 poin
penuh, atau perlu direweight/checknya diganti.

### Temuan lama yang belum dikerjakan (dikonfirmasi masih ada)

#### B1. Peta CIK gagal sekali -> ~16.000 percobaan jaringan — SELESAI
`sec_parser.py::_get_ticker_cik_map`

Memo yang ditambahkan `ca6bf5b` hanya menutup jalur sukses; blok `except`
mengembalikan `{}` tanpa memo dan tanpa negative-cache. Kalau `sec.gov`
membalas 503 di panggilan pertama, setiap `get_cik_from_ticker` — 4x per
ticker x 4065 ticker = ~16.260 panggilan — masuk jalur jaringan lagi, masing-
masing membayar rate limit + 2 percobaan + backoff 3 detik. Minimal ~13,5 jam
tambahan; run tampak menggantung, bukan gagal.

**Perbaikan**: negative-cache dengan cooldown 60 detik — gagal sekali,
jangan coba lagi sampai cooldown lewat (bukan permanent-negative-cache, yang
akan membuat SELURUH run kehilangan data CIK kalau kegagalan pertama cuma
gangguan sesaat). Diverifikasi: 500 panggilan rapid-fire dengan fetch yang
selalu gagal menghasilkan cuma 2 percobaan jaringan (bukan 500), lalu
retry beneran terjadi lagi setelah cooldown lewat; 5 thread x 50 panggilan
konkuren menghasilkan cuma 10 percobaan total.

#### B2. Error non-RequestException di parsing news membuang seluruh paket ticker — SELESAI
`finnhub.py::fetch_company_news`

Blok parsing hanya dijaga `except requests.exceptions.RequestException`.
Kalau Finnhub membalas HTTP 200 dengan objek JSON (bukan list), atau ada item
dengan `"datetime": null`, yang terlempar `AttributeError`/`TypeError` —
lolos sampai `evidence.py`, dan `price_market`, `fundamental`, filing SEC yang
sudah berhasil diambil **ikut dibuang** gara-gara berita.

**Perbaikan**: validasi `data` adalah list sebelum diiterasi (kalau bukan,
diperlakukan sebagai fetch gagal — `status="missing"`). Per-item: skip item
dengan `datetime` None/0/absen atau timestamp yang tidak bisa diparse, alih-
alih membiarkan `datetime.fromtimestamp(None, ...)` melempar `TypeError` yang
membawa turun item-item lain yang valid.

Diverifikasi 3 skenario (respons non-list, item `datetime:null` di tengah
item valid, happy path) — semua menghasilkan status/news_count yang benar,
item cacat di-skip tanpa membuang item valid lainnya.

#### B3. Tidak ada circuit breaker saat Finnhub 403 — SELESAI
Kalau paket Finnhub diturunkan sehingga `company-news` jadi premium, setiap
ticker tetap membayar 1.05 detik rate limit SEBELUM tahu kena 403, dan jalur
403 (benar) tidak menyimpan cache — jadi ~71 menit terbakar tiap run tanpa
menghasilkan apa pun, berulang selamanya.

**Perbaikan**: flag `_403_confirmed` di-set begitu satu respons 403
diterima; panggilan berikutnya short-circuit SEBELUM `_apply_rate_limit()`
— tanpa network call maupun jeda rate limit. Direset tiap run baru (lewat
`reset_batch_tracking()`) supaya upgrade plan di tengah hari terdeteksi
lagi besok. Diverifikasi: call pertama yang 403 + 20 call berikutnya untuk
ticker berbeda menghasilkan total cuma 1 network call, elapsed <1 detik.

#### B4. Cache per-CIK jadi tulis-bersamaan untuk ticker dwi-kelas — SELESAI
Tiga namespace cache (`facts_{cik}`, `submissions_{cik}`, `form4_activity_{cik}`)
dikunci pada CIK, bukan ticker. `screening_result.passed` urut abjad, jadi
GOOG/GOOGL, FOX/FOXA, HEI/HEI-A berdekatan dan hampir pasti berada dalam
jendela 5 worker yang sama -> dua `write_text` bersamaan ke file yang sama.
Self-healing (pembaca dapat parse error -> dianggap cache miss), tapi boros.
Tidak ada saat Evidence masih serial.

**Perbaikan**: `cache.py::set()` sekarang atomik (tmp unik-per-penulis via
`tempfile.mkstemp` + `os.replace`), mengikuti pola yang sudah dipakai
konsisten di tempat lain di codebase — `cache.py` adalah satu-satunya
penulis file yang sebelumnya tidak memakainya. Diverifikasi: stress test 6
writer + 6 reader konkuren ke SATU cache key selama 500 iterasi baca — kode
lama 1864/3000 (62%) pembacaan gagal parse (torn write terkonfirmasi
nyata); kode baru 0 pembacaan korup.

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

#### B6. Kegagalan Yahoo sesaat memalsukan katalis "cancelled" — SELESAI
`catalyst_history.py`

Loop yang meresolusi katalis yang hilang tidak menjaga `cs.status == "missing"`.
Kalau `_fetch_yahoo_info` gagal sekali, `CatalystSet` kosong -> tanggal earnings
yang masih di masa depan ditulis sebagai `lifecycle_status="cancelled"`.
Pembatalan palsu itu permanen dan tampil di UI; katalis aslinya muncul lagi
esok hari sebagai entri "scheduled" baru tanpa kesinambungan.

**Perbaikan**: guard `if cs.status == "missing": continue` di awal iterasi
per-ticker di `sync_catalyst_history` — kegagalan fetch sekarang membiarkan
state tersimpan (`active`/`resolved`) apa adanya, dicoba lagi run berikutnya,
alih-alih menyimpulkan katalisnya sungguh hilang.

Diverifikasi kontras kode lama vs baru lewat simulasi 3 hari (aktif -> Yahoo
gagal sesaat -> Yahoo pulih): kode lama membuat `active={}` dan `resolved`
mencatat "cancelled" untuk katalis 20 hari ke depan di hari gagal; kode baru
`active`/`resolved` tidak berubah di hari gagal, katalis tetap `scheduled`
begitu fetch pulih.

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

#### C1. `/api/ticker/<t>` bisa mencampur data dari dua run berbeda — SELESAI
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

**Perbaikan**: `sync_price_target_history`/`sync_catalyst_history` sekarang
HANYA menghitung store terupdate secara in-memory (tetap melekatkan
accumulated series ke evidence_packages/catalyst_sets seperti biasa, dibutuhkan
Knowledge/reasoning di run yang sama) — pemanggilan `save_price_target_store`/
`save_catalyst_history_store` yang sesungguhnya menulis ke disk dipindah ke
blok "every stage succeeded" di `refresh_full_pipeline.py`, sejajar dengan 10
`_atomic_write` lain. Gagal di tahap manapun sebelum itu kini benar-benar
tidak menyentuh disk untuk kedua file ini. Satu-satunya pemanggil masing-
masing fungsi (diverifikasi via grep) adalah `refresh_full_pipeline.py`.

Diverifikasi: unit test langsung kedua fungsi (file TIDAK ada di disk setelah
`sync_*`, ADA setelah `save_*_store` dipanggil eksplisit, isi round-trip
benar), `py_compile` + `ruff` bersih.

Catatan: ini TIDAK menyelesaikan C2/C9 (blok tulis gerbang itu sendiri tetap
10+ tulis file terpisah, bukan satu transaksi) — itu risiko yang berbeda
(kill eksternal di TENGAH gerbang sukses), bukan yang C1 tangani (dua file
maju duluan SEBELUM gerbang, lalu gerbang gagal total).

#### C2. Blok tulis "gerbang" tetap 10 tulis file terpisah — SELESAI (sebagian)
Masing-masing atomik sendiri (tmp + `os.replace`), tapi tanpa transaksi lintas
file. Kill eksternal di antara dua tulis menghasilkan mis. rekomendasi
Aggregator yang merujuk penilaian Risk dari hari berbeda.

**Keputusan pengguna**: perbaikan ringan (marker + deteksi), bukan transaksi
penuh (staging+swap) yang menyentuh tiap writer dan risikonya lebih besar.
Lihat detail perbaikan dan verifikasi di C9 di bawah (satu commit, satu fix
untuk keduanya). **Ini mendeteksi ketidaksesuaian, tidak mencegahnya** — kill
eksternal di tengah gerbang tetap bisa menghasilkan file campuran, sekarang
cuma jadi TERLIHAT (banner di dashboard + `/api/consistency`) alih-alih diam.

#### C3. `_get_stage` menahan seluruh file JSON di memori selamanya — SELESAI (sebagian)
`backend/app.py` — `json.load` seluruh berkas lalu disimpan permanen di
`_stage_cache`. Flask teramati ~2GB RSS di era evidence.json 340MB.
`historical_timeline.json` kini 469MB dan bertambah tiap run, dan tidak ada
endpoint ringan `/api/historical/summary` (padahal `/api/evidence/summary` ada).

**Update**: endpoint ringan sudah ada (lihat C6 di atas). **Keputusan
pengguna**: batasi pertumbuhan `historical_timeline.json` ke retensi 2 tahun
per ticker, bukan tunda sampai bentuk evaluasi v2.1 diputuskan.

**Yang SELESAI**: pertumbuhan file kini dibatasi (730 hari/ticker, lihat
`historical.py::update_timeline`/`RETENTION_DAYS`) — tidak lagi tumbuh tanpa
batas selamanya. `total_entries`/`first_entry_date` sengaja tetap sebagai
penghitung/tanggal seumur hidup ticker itu dilacak (dipakai StatCards di
dashboard), tidak ikut terpangkas — cuma buffer `entries` (snapshot penuh)
yang dibatasi.

**Yang MASIH TERBUKA**: `_stage_cache` tetap menahan SELURUH file (termasuk
469MB `historical_timeline.json` yang sekarang dibatasi tapi belum otomatis
menyusut sampai retensi baru "menyusul" lewat run-run berikutnya) permanen di
memori, dan `_warm_cache` masih memuat semua 13 stage file penuh saat
startup. Tidak ada lazy-loading/eviction — ini bagian arsitektural yang
belum disentuh, di luar cakupan "batasi pertumbuhan" yang diputuskan.

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

#### C6. `/api/historical` mengirim 469 MB dalam satu respons — SELESAI (sebagian)
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

**Perbaikan**: `/api/historical/summary` baru (pola sama seperti
`/api/evidence/summary`) — backend menghitung `last_halted`/`has_outcome` dari
`entries` sekali, kirim 5 skalar per ticker saja. `HistoricalView.jsx` pindah
ke endpoint ini. Menghilangkan biaya kirim+gzip 469MB PER REQUEST, yang
merupakan risiko paling akut (satu klik nav menggantungkan request lain).

**Belum diselesaikan sepenuhnya**: endpoint baru masih memanggil
`_get_stage("historical")`, jadi backend tetap mem-parse dan menahan seluruh
file di `_stage_cache` — cuma respons ke browser yang mengecil, bukan RSS
Flask. Update: pertumbuhan filenya sendiri sekarang SUDAH dibatasi (retensi
2 tahun/ticker, lihat C3 di bawah) — jadi ukurannya tidak lagi tak terbatas,
tapi `_stage_cache` tetap menahan APAPUN ukurannya (yang sekarang sudah
dibatasi, tapi masih besar) permanen di memori tanpa lazy-loading/eviction.
Itu bagian arsitektural C3 yang belum disentuh.

Terkait C3: total 13 berkas stage kini ~866 MB di disk, dan `_warm_cache`
sengaja memuat **semuanya** saat startup. Sebelum retensi C3 diperbaiki, ini
diproyeksikan gagal dalam ~2 minggu run harian — dengan retensi terbatas,
`historical_timeline.json` tidak lagi jadi kontributor pertumbuhan tak
terbatas, tapi 12 file stage lain (termasuk evidence.json) tetap tumbuh
mengikuti ukuran universe, bukan waktu.

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

#### C9. `18` tulis berurutan, bukan 10 — SELESAI (sebagian, memperjelas C2)
Rentang penulisan terukur 86 detik pada run 2026-07-30 (06:55:12 -> 06:56:38):
10 berkas stage + 2 personal + 3 layer1/source-health + 4 snapshot root.
Masing-masing atomik sendiri; tidak ada yang membuat himpunannya atomik.

**Keputusan pengguna**: perbaikan ringan (marker + deteksi), bukan transaksi
penuh.

**Perbaikan**: `session_id` (satu variabel kanonik yang sudah ada untuk
`aggregator_data`) sekarang ditulis ke SEMUA 9 file stage berwrapper
(screening/evidence/knowledge/catalyst/peer/confidence/risk/reasoning,
aggregator sudah punya) di `refresh_full_pipeline.py`. `historical_timeline.json`
sengaja dikecualikan — bentuknya `{ticker: {...}}` tanpa wrapper level-atas.
Endpoint baru `backend/app.py::GET /api/consistency` membaca `session_id` dari
kesembilan file itu dan melaporkan `consistent` (semua sama) + rincian
per-file. File lama tanpa `session_id` (ditulis sebelum perbaikan ini)
dilaporkan `None`, tidak otomatis dianggap tidak konsisten. Frontend
(`App.jsx`) fetch endpoint ini sekali di startup dan menampilkan banner
peringatan di topbar (lintas semua view) kalau tidak konsisten.

Diverifikasi: `/api/consistency` diuji 4 skenario lewat Flask test client
(tidak ada file, semua `session_id` seragam, disimulasikan run terhenti di
tengah gerbang dengan 3/9 file punya `session_id` baru, dan file lama tanpa
`session_id` sama sekali) — keempatnya benar, termasuk tidak false-positive
untuk file lama.

**Belum diselesaikan** (sesuai keputusan, bukan celah yang terlewat): ini
MENDETEKSI ketidaksesuaian, TIDAK MENCEGAHNYA. Kill eksternal di tengah
gerbang tetap bisa menghasilkan file campuran — sekarang jadi terlihat
(banner + endpoint), bukan diam. Transaksi penuh (staging+swap atomik)
tetap TERBUKA kalau suatu saat mau ditingkatkan.

#### C10. `catalyst_history.json` merusak diri sendiri saat run gagal — SELESAI
Koreksi atas C1: berkas ini **tidak** disajikan backend sama sekali (tidak ada
di `STAGE_FILES`), jadi tidak bisa mencemari respons API. Kerusakannya justru
internal dan lebih halus: `sync_catalyst_history` membandingkan katalis hari ini
dengan state tersimpan untuk menurunkan status delayed/completed/cancelled. Run
yang gagal tetap memajukan state itu tanpa menghasilkan `catalysts.json` yang
sepadan — sehingga run sukses berikutnya membandingkan dengan state yang sudah
"terpakai", dan transisi katalis satu hari **hilang tanpa bisa dideteksi dari
luar**.

**Perbaikan**: sama seperti C1 — `save_catalyst_history_store` dipindah ke
gerbang all-or-nothing, jadi run yang gagal sebelum gerbang tidak lagi
memajukan state "active"/"resolved" internal sama sekali. Lihat detail
perbaikan dan verifikasi di C1 di atas (satu commit, satu fix untuk keduanya).

Catatan lama (sebelum perbaikan) atas C1: `session_id` sebenarnya **sudah** ada
di setiap elemen `recommendations` dan ikut dikembalikan `/api/ticker/<t>`, dan
setiap snapshot price-target punya `date` sendiri — jadi API secara teknis
sudah memancarkan cukup informasi untuk mendeteksi percampuran seandainya ada
konsumen yang memeriksanya. Sekarang sudah tidak relevan lagi karena akar
masalahnya (dua file maju duluan) sudah dihilangkan, bukan cuma dideteksi.

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

SELESAI (penuh atau sebagian sesuai keputusan pengguna): A3, A4, A5, A7, A8
(sebagian), A13, B1, B2, B3, B4, B6, C1, C2, C3, C6, C9, C10. Sisa TERBUKA
yang belum tersentuh sama sekali:

- **B5** — falsy-check 0.0 di 4 tempat sisa (`_fcf_margin_pct`, `fcf_yield`,
  `revenue_yoy_q4`, `capex_pct_revenue_q4`)
- **B7** — CLI `knowledge` mati total (`TypeError`, jalur produksi tidak
  terpengaruh)
- **B8** — dua nilai `fast_info` lolos koersi tipe (laten, belum terjadi)
- **B9** — `.info` kosong di-cache dan dilaporkan `status="ok"`
- **B10** — komentar kontrak salah skala (`institutional_pct`/`insider_pct`)
- **B11** — `personal_evaluation.py` ikut terdampak downsampling
- **A8 (sisa)** — apakah section 1-cek biner pantas mengayunkan skor 5 poin
  penuh; butuh keputusan kalibrasi seperti A7, bukan bug murni
- **C3 (sisa arsitektural)** — `_stage_cache` masih menahan SEMUA stage file
  permanen di memori tanpa lazy-loading/eviction; retensi cuma membatasi
  PERTUMBUHAN `historical_timeline.json`, bukan cara backend menahannya
- **C2/C9 (peningkatan)** — kalau suatu saat mau transaksi penuh
  (staging+swap), bukan cuma deteksi
- **C4** — `_retry.py` bisa kehabisan thread (pool global 32 worker)
- **C7** — `POST /api/refresh/<mode>` tanpa autentikasi di `0.0.0.0`
- **C8** — `save_personal_history` menulis non-atomik
