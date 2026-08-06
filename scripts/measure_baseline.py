"""Berapa hit rate SEMBARANG saham di jendela yang sama? — pembanding nol
untuk lensa Spekulatif.

Kenapa ini ada: "hit rate 34,7%" tidak bisa dibaca tanpa tahu berapa yang
didapat tanpa seleksi sama sekali. Peluang sebuah saham naik >=3% dalam 5-8
hari itu sendiri sudah besar (gerakan khas di jendela itu ±5,16%), jadi
angka 34,7% bisa berarti "seleksinya bekerja" atau "seleksinya tidak
menambah apa-apa" — dua kesimpulan yang berlawanan, dari angka yang sama.

Metodenya: untuk TIAP jendela (entry_date -> exit_date) yang benar-benar
dipakai mengevaluasi tesis, hitung return semua ticker yang punya bar di
kedua tanggal itu, lalu bandingkan dua hal pada jendela yang sama persis:

  - hit rate pilihan sistem  (tesis yang benar-benar dikeluarkan)
  - hit rate seluruh universe (pembanding nol)

Kedua sisi dihitung dari SUMBER HARGA YANG SAMA (.cache/price_history) dan
dengan cara yang sama. Ini disengaja: outcome tersimpan memakai
`price_at_call`/rekonstruksi dan `last_price`, yang tidak identik dengan
bar penutupan, dan membandingkan angka dari dua metode berbeda akan
menghasilkan selisih yang bukan berasal dari seleksi.

Nol jaringan. Tidak menulis apa pun ke dashboard/data.

Pakai:
    python scripts/measure_baseline.py

CATATAN KINERJA (dipelajari dengan cara yang mahal, 2026-08-05): versi
pertama membaca SELURUH 5.265 file cache harga (440 MB) dan seluruh
personal_history.json (141 MB) tiap kali dijalankan. Di mesin ini itu tidak
selesai dalam 30 menit — CPU-nya cuma terpakai 11 detik, sisanya menunggu
disk (dugaan kuat: pemindaian antivirus per file), dan seluruh mesin ikut
tercekik sampai `tasklist` pun timeout. Dua penawarnya ada di bawah:

  - Universe-nya DISAMPEL (SAMPLE_SIZE), bukan dibaca semua. Untuk mengukur
    base rate, 800 sampel memberi ketelitian sekitar +-1,7pp pada 95% —
    jauh lebih dari cukup untuk membedakan "sekitar 35%" dari "sekitar 20%",
    dan itu satu-satunya perbedaan yang perlu dijawab di sini. Sampelnya
    diambil dengan seed tetap supaya hasilnya bisa diulang persis.
  - Jendela + daftar pilihan diekstrak SEKALI ke berkas kecil (WINDOWS_CACHE),
    jadi berkas riwayat 141 MB tidak dibaca ulang saat skrip ini diubah-ubah.

Jalankan TANPA menyalurkan ke `tail`/`head`: pipa menahan seluruh cetakan
sampai proses selesai, jadi progresnya tidak kelihatan sama sekali (dan ikut
hilang kalau prosesnya dihentikan).
"""
from __future__ import annotations

import json
import os
import random
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CACHE = ROOT / ".cache" / "price_history"
HISTORY = ROOT / "dashboard" / "data" / "personal" / "personal_history.json"
BENCHMARK = ROOT / "dashboard" / "data" / "benchmark_history.json"
WINDOWS_CACHE = ROOT / ".cache" / "baseline_windows.json"

SAMPLE_SIZE = 800
SEED = 20260805


def load_bars(ticker: str) -> dict[str, float]:
    p = CACHE / f"{ticker}.json"
    if not p.exists():
        # cache.py memberi prefiks "_" ke nama yang haram di Windows (CON, PRN,
        # AUX, NUL, COM1-9, LPT1-9) -- CON adalah ticker NYSE yang nyata.
        p = CACHE / f"_{ticker}.json"
        if not p.exists():
            return {}
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out = {}
    for bar in payload.get("data") or []:
        d, c = bar.get("__date__"), bar.get("Close")
        if d and isinstance(c, (int, float)):
            out[str(d)[:10]] = float(c)
    return out


def ret(bars: dict[str, float], a: str, b: str) -> float | None:
    """Return % antara dua tanggal. Butuh bar PERSIS di kedua tanggal — tidak
    mundur ke bar terdekat, supaya jendela pilihan sistem dan jendela
    pembanding benar-benar sama panjang. Ticker yang tidak punya salah satunya
    dikeluarkan dari kedua sisi, bukan diisi asumsi."""
    pa, pb = bars.get(a), bars.get(b)
    if not pa or pb is None:
        return None
    return (pb - pa) / pa * 100.0


def extract_windows() -> dict[str, list[str]]:
    """{"entry|exit|threshold": [ticker, ...]} — jendela yang benar-benar
    dipakai mengevaluasi tesis, plus ticker mana saja yang dipilih sistem di
    tiap jendela. Di-cache ke berkas kecil karena sumbernya 141 MB."""
    if WINDOWS_CACHE.exists():
        print(f"Jendela dibaca dari cache {WINDOWS_CACHE.name} (hapus berkas ini kalau riwayat berubah)")
        return json.loads(WINDOWS_CACHE.read_text(encoding="utf-8"))

    print(f"Mengekstrak jendela dari {HISTORY.name} ({HISTORY.stat().st_size // 2**20} MB, sekali saja) ...")
    data = json.loads(HISTORY.read_text(encoding="utf-8"))
    timelines = data.get("timelines", data)

    windows: dict[str, set[str]] = defaultdict(set)
    seen: set[str] = set()
    for ticker, tl in timelines.items():
        for e in (tl.get("entries", []) if isinstance(tl, dict) else []):
            for module, r in (e.get("outcome") or {}).items():
                if not isinstance(r, dict) or r.get("classification") not in ("terbukti", "meleset"):
                    continue
                key = f"{ticker}:{module}:{r.get('thesis_key')}"
                if key in seen:
                    continue
                seen.add(key)
                ed, xd, thr = r.get("entry_date"), r.get("exit_date"), r.get("threshold_pct")
                if not ed or not xd or thr is None:
                    continue
                windows[f"{ed[:10]}|{xd[:10]}|{float(thr)}"].add(ticker)

    out = {k: sorted(v) for k, v in windows.items()}
    WINDOWS_CACHE.parent.mkdir(parents=True, exist_ok=True)
    WINDOWS_CACHE.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"  {len(out)} jendela disimpan ke {WINDOWS_CACHE.name}")
    return out


def main() -> int:
    raw = extract_windows()
    windows = {}
    for k, tickers in raw.items():
        ed, xd, thr = k.split("|")
        windows[(ed, xd, float(thr))] = set(tickers)

    if not windows:
        print("Tidak ada tesis dengan jendela lengkap.")
        return 1

    universe = sorted(f[:-5].lstrip("_") for f in os.listdir(CACHE) if f.endswith(".json"))
    picked = set().union(*windows.values())
    # Sampel acak universe + SEMUA ticker yang dipilih sistem (yang terakhir
    # wajib utuh; kalau ikut disampel, sisi "pilihan sistem" jadi ikut
    # berkurang dan perbandingannya bukan lagi antara seleksi vs acak).
    rng = random.Random(SEED)
    sample = set(rng.sample(universe, min(SAMPLE_SIZE, len(universe)))) | picked
    all_tickers = sorted(sample)
    print(f"{len(windows)} jendela unik · universe {len(universe)} ticker "
          f"-> disampel {SAMPLE_SIZE} + {len(picked)} pilihan sistem = {len(all_tickers)} dibaca")

    bench = {}
    if BENCHMARK.exists():
        bench = json.loads(BENCHMARK.read_text(encoding="utf-8")).get("series") or {}

    print("\nMemuat bar harga (sekali, dipakai semua jendela) ...")
    bars_by_ticker = {}
    for i, t in enumerate(all_tickers, 1):
        b = load_bars(t)
        if b:
            bars_by_ticker[t] = b
        if i % 200 == 0:
            print(f"  {i}/{len(all_tickers)} ...", flush=True)
    print(f"  {len(bars_by_ticker)} ticker punya bar")
    universe_bars = {t: b for t, b in bars_by_ticker.items() if t not in picked}

    tot_pick_hit = tot_pick_n = tot_uni_hit = tot_uni_n = 0
    pick_returns: list[float] = []
    uni_returns: list[float] = []

    print("\n" + "=" * 96)
    print(f"{'jendela':<26} {'hari':>4} {'idx%':>6} | {'PILIHAN SISTEM':>22} | {'SEMBARANG SAHAM':>22} | {'selisih':>8}")
    print("=" * 96)

    skipped = []
    for w in sorted(windows):
        ed, xd, thr = w
        # Jendela mundur: satu record punya exit_date (23 Jun) LEBIH AWAL dari
        # entry_date (27 Jul) -- sisa backfill harga lama yang mencari bar
        # dengan mencocokkan harga, lalu ketemu bar yang salah. Cuma 1 tesis,
        # tapi pembanding universe-nya membawa 737 baris dengan jendela mundur
        # 34 hari, dan itu ikut ditotal -- cukup untuk menggeser vonis
        # keseluruhan. Dibuang di sini dan dilaporkan, bukan didiamkan.
        if date.fromisoformat(xd) <= date.fromisoformat(ed):
            skipped.append((ed, xd, len(windows[w])))
            continue
        picks = windows[w]
        bret = None
        if bench.get(ed) and bench.get(xd):
            bret = (bench[xd] - bench[ed]) / bench[ed] * 100.0

        p_rets = [r for t in picks if (r := ret(bars_by_ticker.get(t, {}), ed, xd)) is not None]
        # Pembandingnya ticker yang TIDAK dipilih sistem — dua kelompok saling
        # lepas, jadi selisihnya benar-benar "dipilih vs tidak dipilih", bukan
        # kelompok pilihan yang ikut mengencerkan pembandingnya sendiri.
        u_rets = [r for b in universe_bars.values() if (r := ret(b, ed, xd)) is not None]
        if not p_rets or not u_rets:
            continue

        p_hit = sum(1 for r in p_rets if r >= thr)
        u_hit = sum(1 for r in u_rets if r >= thr)
        p_pct, u_pct = p_hit / len(p_rets) * 100, u_hit / len(u_rets) * 100

        tot_pick_hit += p_hit; tot_pick_n += len(p_rets)
        tot_uni_hit += u_hit; tot_uni_n += len(u_rets)
        pick_returns += p_rets; uni_returns += u_rets

        days = (date.fromisoformat(xd) - date.fromisoformat(ed)).days
        print(f"{ed} -> {xd}  {days:>4} {('%+.2f' % bret) if bret is not None else '   -':>6} | "
              f"{p_hit:>4}/{len(p_rets):<4} {p_pct:>6.1f}%      | "
              f"{u_hit:>5}/{len(u_rets):<5} {u_pct:>6.1f}%    | {p_pct - u_pct:>+7.1f}pp")

    print("=" * 96)
    P = tot_pick_hit / tot_pick_n * 100
    U = tot_uni_hit / tot_uni_n * 100
    print(f"{'TOTAL':<26} {'':>4} {'':>6} | {tot_pick_hit:>4}/{tot_pick_n:<4} {P:>6.1f}%      | "
          f"{tot_uni_hit:>5}/{tot_uni_n:<5} {U:>6.1f}%    | {P - U:>+7.1f}pp")

    # --- Aturan v2 pada kedua sisi -------------------------------------------
    # Wajib. Angka v2 untuk pilihan sistem SENDIRIAN tidak bisa dibaca: kalau
    # ternyata `meleset` jauh lebih banyak dari `terbukti`, itu bisa berarti
    # seleksinya buruk ATAU berarti saham median memang tertinggal dari indeks
    # kapitalisasi-tertimbang di jendela itu (efek keluasan pasar, tidak ada
    # hubungannya dengan seleksi). Cuma pembanding pada jendela yang sama yang
    # bisa memisahkan keduanya -- persis alasan seluruh skrip ini ada.
    from alphaforge.personal.personal_evaluation import classify_v2, window_sigma_pct

    def v2_tally(bars_map, tickers, ed, xd, bret):
        t = Counter()
        for tk in tickers:
            b = bars_map.get(tk)
            if not b:
                continue
            r = ret(b, ed, xd)
            if r is None or bret is None:
                continue
            hist = [{"date": d, "close": c} for d, c in sorted(b.items())]
            sig = window_sigma_pct(hist, ed, xd)
            cls, _ = classify_v2("masuk_spekulatif", r - bret, sig)
            t[str(cls)] += 1
        return t

    print("\n" + "=" * 96)
    print("ATURAN v2 (excess vs indeks, dibagi volatilitas saham itu sendiri; terbukti = z >= +1)")
    print("=" * 96)
    pv, uv = Counter(), Counter()
    for w in sorted(windows):
        ed, xd, _ = w
        if date.fromisoformat(xd) <= date.fromisoformat(ed):
            continue
        if not (bench.get(ed) and bench.get(xd)):
            continue
        bret = (bench[xd] - bench[ed]) / bench[ed] * 100.0
        pv += v2_tally(bars_by_ticker, windows[w], ed, xd, bret)
        uv += v2_tally(universe_bars, universe_bars.keys(), ed, xd, bret)

    def show(name, c):
        tb, ml, am = c["terbukti"], c["meleset"], c["ambigu"]
        n = tb + ml + am
        if not n:
            print(f"{name:22s} tidak ada data")
            return None
        print(f"{name:22s} terbukti {tb:5d} ({tb/n*100:5.1f}%) · meleset {ml:5d} ({ml/n*100:5.1f}%) · "
              f"ambigu {am:5d} ({am/n*100:5.1f}%)  dari {n}")
        return tb / n * 100, ml / n * 100, n

    a = show("pilihan sistem", pv)
    b = show("sembarang saham", uv)
    if a and b:
        print(f"\nselisih 'terbukti' : {a[0] - b[0]:+.1f}pp    selisih 'meleset' : {a[1] - b[1]:+.1f}pp")
        p1, p2 = a[0] / 100, b[0] / 100
        n1, n2 = a[2], b[2]
        pool = (p1 * n1 + p2 * n2) / (n1 + n2)
        se2 = (pool * (1 - pool) * (1 / n1 + 1 / n2)) ** 0.5 * 100
        d2 = a[0] - b[0]
        print(f"selang kepercayaan 95% untuk selisih 'terbukti': "
              f"{d2 - 1.96 * se2:+.1f}pp .. {d2 + 1.96 * se2:+.1f}pp")
        if d2 - 1.96 * se2 <= 0 <= d2 + 1.96 * se2:
            print("-> masih tidak bisa dibedakan dari kebetulan, sekarang dengan alat yang lebih tajam.")
        else:
            print("-> DI LUAR derau: seleksi benar-benar berbeda dari acak di bawah aturan v2.")

        # --- UJI PEMISAH: besarnya gerakan vs ARAH gerakan ---------------------
        # Selisih 'terbukti' di atas saja MENYESATKAN kalau dibaca sebagai
        # keunggulan meramal. Pilihan sistem punya LEBIH BANYAK terbukti DAN
        # lebih banyak meleset sekaligus -- artinya |z| >= 1 lebih sering
        # terjadi pada pilihannya, yaitu sahamnya bergerak lebih besar relatif
        # terhadap deru historisnya sendiri. Itu kemampuan mendeteksi PERISTIWA
        # (lensa ini memang memilih ticker berkatalis, 80% earnings), bukan
        # kemampuan menebak ARAH.
        #
        # Dua pertanyaan itu harus dipisah, dan cuma yang kedua yang menentukan
        # apakah action "masuk" masuk akal:
        #   (1) seberapa sering gerakannya besar   -> pangsa |z| >= 1
        #   (2) KALAU besar, seberapa sering NAIK  -> terbukti / (terbukti+meleset)
        def big_and_dir(name, c):
            tb, ml, am = c["terbukti"], c["meleset"], c["ambigu"]
            n, big = tb + ml + am, tb + ml
            if not n or not big:
                return None
            print(f"  {name:20s} gerakan besar {big:5d}/{n:<5d} ({big/n*100:5.1f}%)   "
                  f"| kalau besar, naik: {tb:4d}/{big:<5d} ({tb/big*100:5.1f}%)")
            return big / n, tb / big, n, big

        print("\n" + "-" * 96)
        print("PEMISAHAN: mendeteksi gerakan BESAR vs menebak ARAH-nya")
        print("-" * 96)
        pa = big_and_dir("pilihan sistem", pv)
        ub = big_and_dir("sembarang saham", uv)
        if pa and ub:
            for label, (x1, n1_), (x2, n2_) in (
                ("pangsa gerakan besar", (pa[0], pa[2]), (ub[0], ub[2])),
                ("arah naik | besar", (pa[1], pa[3]), (ub[1], ub[3])),
            ):
                pl = (x1 * n1_ + x2 * n2_) / (n1_ + n2_)
                s = (pl * (1 - pl) * (1 / n1_ + 1 / n2_)) ** 0.5 * 100
                d = (x1 - x2) * 100
                sig = "DI LUAR derau" if abs(d) > 1.96 * s else "di dalam derau"
                print(f"  {label:24s} selisih {d:+6.1f}pp  SK95% {d - 1.96 * s:+6.1f}..{d + 1.96 * s:+6.1f}pp  -> {sig}")

    print(f"\nmedian return pilihan sistem : {statistics.median(pick_returns):+.2f}%")
    print(f"median return sembarang saham: {statistics.median(uni_returns):+.2f}%")
    print(f"selisih median               : {statistics.median(pick_returns) - statistics.median(uni_returns):+.2f}pp")

    if skipped:
        print("\nJendela dibuang (exit_date <= entry_date, sisa backfill harga lama):")
        for ed, xd, n in skipped:
            print(f"  {ed} -> {xd}  ({n} tesis)")

    # Selisih dua proporsi + galat bakunya. Versi pertama skrip ini memvonis
    # "memilih lebih buruk daripada tidak memilih" hanya karena selisihnya
    # negatif >2pp -- padahal dengan n=309 di sisi pilihan, selisih sebesar itu
    # masih di dalam derau. Menyatakan kalah tanpa menghitung galatnya adalah
    # kesalahan yang sama persis dengan menyatakan menang tanpa menghitungnya.
    p1, n1 = tot_pick_hit / tot_pick_n, tot_pick_n
    p2, n2 = tot_uni_hit / tot_uni_n, tot_uni_n
    pool = (tot_pick_hit + tot_uni_hit) / (n1 + n2)
    se = (pool * (1 - pool) * (1 / n1 + 1 / n2)) ** 0.5 * 100
    diff = P - U
    lo, hi = diff - 1.96 * se, diff + 1.96 * se

    print("\n--- VONIS ---")
    print(f"Selisih hit rate: {diff:+.1f}pp  (galat baku {se:.1f}pp, "
          f"selang kepercayaan 95%: {lo:+.1f}pp .. {hi:+.1f}pp)")
    if lo <= 0 <= hi:
        print("\nNOL ADA DI DALAM SELANG -> seleksi Spekulatif TIDAK menunjukkan keunggulan")
        print("maupun kekalahan yang bisa dibedakan dari kebetulan. Dengan kata lain:")
        print("memilih dengan lensa ini sejauh ini SAMA SAJA dengan mengambil saham")
        print("sembarangan dari universe yang sama pada jendela yang sama.")
    elif lo > 0:
        print("\nSeleksi Spekulatif UNGGUL, dan selisihnya di luar derau.")
    else:
        print("\nSeleksi Spekulatif TERTINGGAL, dan selisihnya di luar derau.")

    print("\nDua peringatan yang TIDAK boleh dilepas dari angka di atas:")
    print(f"  1. Cuma dari 3 tanggal masuk. Selang kepercayaan di atas menganggap")
    print(f"     {n1} tesis itu saling bebas; kenyataannya mereka 3 taruhan yang")
    print(f"     disebar, jadi selang yang sebenarnya LEBIH LEBAR dari yang tertulis.")
    print(f"  2. Base rate universe {U:.1f}% memberi tahu sesuatu yang lain: naik >=3%")
    print(f"     dalam 4-9 hari itu memang sering terjadi tanpa seleksi apa pun.")
    print(f"     Target 3% berada DI BAWAH derau harga biasa, jadi vonis")
    print(f"     terbukti/meleset lebih banyak menangkap volatilitas ketimbang tesis.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
