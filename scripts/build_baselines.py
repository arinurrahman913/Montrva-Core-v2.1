"""Pembanding untuk Rapor Kalibrasi: lensa vs kalender vs acak.

Kenapa ada: `calibration.json` selama ini melaporkan hit rate, irisan, dan
gerbang bukti -- tapi tidak pernah memuat jawaban atas pertanyaan "dibandingkan
APA?". Pembanding acak sudah pernah dihitung (`scripts/measure_baseline.py`),
tapi hidupnya di skrip manual yang hasilnya tidak pernah mendarat di rapor,
jadi halaman yang tugasnya menjawab "sistem ini terbukti atau belum" berdiri
tanpa titik nol.

Dan acak saja ternyata pembanding yang TERLALU LEMAH. Diukur 2026-08-08: lensa
Spekulatif memilih 93% ticker berkatalis <=7 hari, sementara universe cuma 26%
-- artinya sebagian besar keunggulannya atas acak bisa jadi cuma "memilih
perusahaan yang lapor earnings lusa", sesuatu yang bisa ditiru satu baris
pencarian kalender tanpa satu pun faktor skor. Karena itu lengan `kalender`
ada di sini: ia mistar yang jauh lebih sulit, dan justru itu gunanya.

Tiga lengan, jendela IDENTIK, definisi z dari fungsi PRODUKSI:

  lensa    -- ticker yang benar-benar dipilih lensa Spekulatif
  kalender -- semua ticker dengan katalis <= NAIVE_MAX_DAYS hari dari entry,
              tanpa melihat skor apa pun
  acak     -- sampel acak universe, kontrol kewarasan: kalau `kalender` tidak
              unggul dari `acak`, yang rusak rekonstruksinya, bukan lensanya,
              dan angka apa pun di atasnya tidak boleh dipercaya

Sengaja BUKAN bagian dari refresh_full_pipeline.py: pembacaan bar harga di
mesin ini ~100 berkas/menit (dugaan: pemindaian AV per berkas), jadi
menempelkannya ke tiap run berarti menambah belasan menit ke pipeline yang
sudah 3 jam. Jalankan manual saat ingin memperbarui mistarnya;
personal_calibration.py membaca hasilnya best-effort dan melaporkan tanggalnya
supaya kebasian terlihat, bukan tersembunyi.

    python scripts/build_baselines.py
"""
from __future__ import annotations

import json
import math
import os
import random
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from alphaforge.personal.personal_evaluation import window_sigma_pct, Z_TERBUKTI  # noqa: E402

CACHE = ROOT / ".cache" / "price_history"
DATA = ROOT / "dashboard" / "data"
PERSONAL = DATA / "personal"
OUT = PERSONAL / "baselines.json"

# Jarak katalis untuk lengan kalender. BUKAN hasil pencarian angka yang
# membuat lensa menang/kalah -- diambil dari median jarak katalis pilihan
# lensa yang diukur SEBELUM uji ini pernah dijalankan (median 2 hari di
# keempat tanggal masuk). Mengubahnya sesudah melihat hasil = memilih mistar
# yang memberi jawaban yang diinginkan.
NAIVE_MAX_DAYS = 2

# Jendela dengan peserta di bawah ini dibuang: rate dari belasan ticker
# berayun terlalu liar untuk berarti, dan ikut menggeser angka gabungan.
MIN_WINDOW_TICKERS = 20

# Sampel acak ditarik SEKALI lalu dipakai di semua jendela -- pola yang sama
# dengan scripts/measure_baseline.py, dan itu bukan kebetulan. Versi pertama
# skrip ini menarik sampel baru per jendela; hasilnya tiap jendela membawa
# ratusan berkas yang belum pernah dibaca, dan run-nya menghabiskan 40 menit
# dengan CPU cuma 8 detik -- sepenuhnya tertahan disk. Sampel tetap juga lebih
# bersih secara statistik: kelompok kontrol yang SAMA diukur di tiap jendela,
# jadi selisih antar-jendela tidak tercampur selisih antar-sampel.
#
# Ukuran sampel dipilih dari BIAYA, bukan dari statistik -- statistiknya sudah
# jauh lebih dari cukup jauh di bawah ini. Pembacaan bar DINGIN di mesin ini
# ~2 berkas/detik (pemindaian AV per berkas; berkas yang sudah hangat 14/detik,
# dan selisih 7x itulah yang bikin perkiraan awal meleset berjam-jam). Dengan
# 200 per lengan: ~400 pembacaan dingin ~= 3 menit, lalu jendela berikutnya
# gratis karena _BARS sudah terisi.
#
# Presisinya: n=200 per lengan x 7 jendela = 1.400 pengukuran gabungan ->
# galat baku ~1,3pp di p=0,5. Selisih yang layak ditindaklanjuti di sini
# ukurannya belasan pp, jadi 1,3pp lebih dari memadai.
RANDOM_SAMPLE_SIZE = 200

# Lengan kalender juga dibatasi, dengan alasan yang sama. Subsampelnya
# di-seed, jadi hasilnya bisa direproduksi persis.
CALENDAR_SAMPLE_SIZE = 200
SEED = 20260808

_BARS: dict[str, list[dict] | None] = {}


def load_bars(ticker: str) -> list[dict] | None:
    """Bar harga, DINORMALKAN ke {"date","close"} dan di-cache per proses.

    Normalisasinya wajib, bukan kerapian: berkas cache menulis "__date__"/
    "Close" sedangkan `window_sigma_pct` produksi membaca "date"/"close" (di
    pipeline ia menerima bentuk Evidence yang sudah dinormalkan, bukan cache
    mentah). Tanpa konversi ini fungsi itu mengembalikan None untuk SETIAP
    ticker tanpa satu pun error -- seluruh pengukuran menghasilkan tabel
    kosong yang terlihat seperti "tidak ada data", bukan seperti bug.

    Cache-nya juga wajib: tanpa itu tiap jendela membaca ulang berkas yang
    sama, ~6.000 pembacaan untuk ~1.500 ticker unik.
    """
    if ticker in _BARS:
        return _BARS[ticker]
    p = CACHE / f"{ticker}.json"
    if not p.exists():
        # cache.py memberi prefiks "_" ke nama yang haram di Windows (CON,
        # PRN, AUX, NUL, COM1-9, LPT1-9) -- CON ticker NYSE yang nyata.
        p = CACHE / f"_{ticker}.json"
        if not p.exists():
            _BARS[ticker] = None
            return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8")).get("data") or []
        out = [
            {"date": str(b["__date__"])[:10], "close": float(b["Close"])}
            for b in raw
            if b.get("__date__") and isinstance(b.get("Close"), (int, float))
        ] or None
    except Exception:
        out = None
    _BARS[ticker] = out
    return out


def _return_pct(bars: list[dict], entry: str, exit_: str) -> float | None:
    """Butuh bar PERSIS di kedua tanggal -- tidak mundur ke bar terdekat,
    supaya jendela ketiga lengan benar-benar sama panjang. Ticker yang tidak
    punya salah satunya keluar dari penyebut, bukan diisi asumsi."""
    m = {b["date"]: b["close"] for b in bars}
    a, b = m.get(entry), m.get(exit_)
    return None if not a or b is None else (b - a) / a * 100.0


def _catalyst_dates() -> dict[str, set[str]]:
    """Semua tanggal katalis yang diketahui per ticker (aktif + yang sudah
    lewat), dari catalyst_history.json."""
    path = DATA / "catalyst_history.json"
    if not path.exists():
        return {}
    ch = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, set[str]] = defaultdict(set)
    for t, kinds in (ch.get("active") or {}).items():
        for _kind, c in (kinds or {}).items():
            if c.get("expected_at"):
                out[t].add(c["expected_at"][:10])
    for t, recs in (ch.get("resolved") or {}).items():
        for c in recs or []:
            if c.get("expected_at"):
                out[t].add(c["expected_at"][:10])
    return out


def _windows(timelines: dict) -> dict[tuple[str, str], set[str]]:
    """(entry, exit) -> ticker yang dipilih lensa Spekulatif di jendela itu.

    Satu tesis = satu (ticker, thesis_key); entry harian yang berbagi tesis
    yang sama membawa outcome identik, jadi didedupe supaya ticker tidak
    terhitung berkali-kali.
    """
    seen: set[tuple[str, str]] = set()
    out: dict[tuple[str, str], set[str]] = defaultdict(set)
    for ticker, tl in timelines.items():
        for e in (tl.get("entries") or []):
            oc = (e.get("outcome") or {}).get("speculative")
            if not oc:
                continue
            key = (ticker, oc.get("thesis_key"))
            if key in seen:
                continue
            seen.add(key)
            ed, xd = oc.get("entry_date"), oc.get("exit_date")
            if ed and xd and oc.get("z_excess") is not None:
                out[(ed[:10], xd[:10])].add(ticker)
    return out


def _rate(tickers, entry: str, exit_: str, bench: dict, label: str = "") -> tuple[int, int]:
    """(n terukur, jumlah |z| >= Z_TERBUKTI). Ticker yang z-nya tak terhitung
    dibuang dari penyebut -- sama seperti jalur produksi, yang menulis vonis
    v2 kosong alih-alih menebak."""
    be, bx = bench.get(entry), bench.get(exit_)
    bret = ((bx - be) / be * 100.0) if (be and bx) else 0.0
    n = hit = 0
    total = len(tickers)
    for i, t in enumerate(tickers, 1):
        if i % 200 == 0:
            print(f"      {label} {i}/{total} ...", flush=True)
        bars = load_bars(t)
        if not bars:
            continue
        r = _return_pct(bars, entry, exit_)
        if r is None:
            continue
        sigma = window_sigma_pct(bars, entry, exit_)
        if not sigma:
            continue
        if abs((r - bret) / sigma) >= Z_TERBUKTI:
            hit += 1
        n += 1
    return n, hit


def _stat(n: int, hit: int) -> dict:
    p = hit / n if n else None
    se = math.sqrt(p * (1 - p) / n) if n and p is not None else None
    return {
        "n": n,
        "hits": hit,
        "rate_pct": round(p * 100, 1) if p is not None else None,
        "stderr_pp": round(se * 100, 1) if se is not None else None,
    }


def _compare(a: dict, b: dict, label_a: str, label_b: str) -> dict:
    """Selisih dua lengan + SK95%. `verdict` sengaja kosakata tertutup supaya
    pembacanya tidak menafsir sendiri angka yang memuat nol."""
    if not a["n"] or not b["n"] or a["rate_pct"] is None or b["rate_pct"] is None:
        return {"pair": f"{label_a} vs {label_b}", "verdict": "tak_terukur"}
    d = (a["rate_pct"] - b["rate_pct"]) / 100.0
    se = math.sqrt((a["stderr_pp"] / 100.0) ** 2 + (b["stderr_pp"] / 100.0) ** 2)
    lo, hi = d - 1.96 * se, d + 1.96 * se
    verdict = ("unggul" if lo > 0 else "kalah" if hi < 0 else "setara")
    return {
        "pair": f"{label_a} vs {label_b}",
        "diff_pp": round(d * 100, 1),
        "ci95_lo_pp": round(lo * 100, 1),
        "ci95_hi_pp": round(hi * 100, 1),
        "verdict": verdict,
    }


def main() -> int:
    hist_path = PERSONAL / "personal_history.json"
    if not hist_path.exists():
        print(f"{hist_path.name} belum ada -- jalankan pipeline dulu.")
        return 1

    bench = {}
    bpath = DATA / "benchmark_history.json"
    if bpath.exists():
        bench = json.loads(bpath.read_text(encoding="utf-8")).get("series") or {}
    if not bench:
        print("PERINGATAN: benchmark_history.json kosong -- z dihitung tanpa koreksi indeks.")

    cat = _catalyst_dates()
    print(f"tanggal katalis diketahui untuk {len(cat)} ticker")

    print(f"memuat {hist_path.name} ({hist_path.stat().st_size // 2**20} MB) ...", flush=True)
    timelines = json.loads(hist_path.read_text(encoding="utf-8"))
    windows = {w: t for w, t in _windows(timelines).items() if len(t) >= MIN_WINDOW_TICKERS}
    del timelines

    # Jendela mundur (exit <= entry) dibuang dan DILAPORKAN, bukan didiamkan:
    # sisa backfill harga lama pernah menghasilkan exit_date lebih awal dari
    # entry_date, dan lengan pembandingnya membawa ratusan baris jendela
    # mundur yang ikut menggeser angka gabungan.
    reversed_ = [w for w in windows if date.fromisoformat(w[1]) <= date.fromisoformat(w[0])]
    for w in reversed_:
        del windows[w]
    if not windows:
        print("Tidak ada jendela yang layak diukur.")
        return 1
    print(f"jendela dipakai: {len(windows)}"
          + (f" ({len(reversed_)} dibuang karena mundur)" if reversed_ else ""))

    universe = sorted(f[:-5].lstrip("_") for f in os.listdir(CACHE) if f.endswith(".json"))
    rng = random.Random(SEED)
    rand = set(rng.sample(universe, min(RANDOM_SAMPLE_SIZE, len(universe))))
    print(f"universe {len(universe)} ticker · sampel acak tetap {len(rand)}")

    # Himpunan kalender hanya bergantung pada TANGGAL ENTRY, bukan exit -- dan
    # beberapa jendela berbagi entry yang sama. Dihitung sekali per entry
    # supaya jendela kedua dengan entry yang sama tidak membayar apa pun lagi.
    naive_by_entry: dict[str, set[str]] = {}

    tot = {"lensa": [0, 0], "kalender": [0, 0], "acak": [0, 0]}
    per_window = []
    for wi, (entry, exit_) in enumerate(sorted(windows), 1):
        print(f"  [{wi}/{len(windows)}] {entry} -> {exit_}", flush=True)
        picked = windows[(entry, exit_)]
        if entry not in naive_by_entry:
            e = date.fromisoformat(entry)
            full = sorted(
                t for t, ds in cat.items()
                if any(0 <= (date.fromisoformat(d) - e).days <= NAIVE_MAX_DAYS for d in ds)
            )
            # Subsampel di-seed per tanggal entry: hasilnya reproducible, dan
            # tiap entry dapat sampel sendiri (bukan potongan pertama secara
            # alfabet, yang akan bias ke ticker berhuruf awal A-C).
            picker = random.Random(f"{SEED}:{entry}")
            naive_by_entry[entry] = (
                set(picker.sample(full, CALENDAR_SAMPLE_SIZE))
                if len(full) > CALENDAR_SAMPLE_SIZE else set(full)
            )
            print(f"    (kalender {entry}: {len(full)} kandidat -> {len(naive_by_entry[entry])} disampel)", flush=True)
        naive = naive_by_entry[entry]

        row = {"entry": entry, "exit": exit_}
        for name, group in (("lensa", picked), ("kalender", naive), ("acak", rand)):
            print(f"    {name}: {len(group)} ticker", flush=True)
            n, hit = _rate(group, entry, exit_, bench, name)
            tot[name][0] += n
            tot[name][1] += hit
            row[name] = _stat(n, hit)
        per_window.append(row)
        print(f"  {entry}->{exit_}  "
              + "  ".join(f"{k} {row[k]['rate_pct']}% (n={row[k]['n']})" for k in tot),
              flush=True)

    arms = {name: _stat(n, hit) for name, (n, hit) in tot.items()}
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "metric": f"|z| >= {Z_TERBUKTI} (bergerak melebihi deru jendelanya sendiri)",
        "naive_max_days": NAIVE_MAX_DAYS,
        "windows": len(windows),
        "windows_skipped_reversed": len(reversed_),
        "arms": arms,
        "comparisons": [
            _compare(arms["lensa"], arms["kalender"], "lensa", "kalender"),
            _compare(arms["lensa"], arms["acak"], "lensa", "acak"),
            _compare(arms["kalender"], arms["acak"], "kalender", "acak"),
        ],
        "per_window": per_window,
        "note": (
            "kalender = saringan satu baris 'katalis <= N hari dari entry', tanpa skor apa pun. "
            "Kalau lensa cuma SETARA dengannya, faktor-faktor skor tidak menambah apa pun di atas "
            "pencarian kalender. Lengan acak adalah pemeriksa kewarasan: kalau kalender tidak "
            "unggul darinya, rekonstruksinya yang rusak, bukan lensanya."
        ),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\n" + "=" * 62)
    for name, s in arms.items():
        print(f"  {name:<9} n={s['n']:<6} |z|>=1 = {s['rate_pct']}%  (galat baku {s['stderr_pp']}pp)")
    for c in payload["comparisons"]:
        if c.get("verdict") == "tak_terukur":
            print(f"  {c['pair']}: tak terukur")
        else:
            print(f"  {c['pair']}: {c['diff_pp']:+}pp  SK95% {c['ci95_lo_pp']:+} .. {c['ci95_hi_pp']:+}  -> {c['verdict'].upper()}")
    print(f"\nditulis ke {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
