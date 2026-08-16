"""Uji katalis lockup IPO — memakai NAMA FIELD PRODUKSI, bukan nama karangan.

Ada karena bug yang lolos ke produksi 15 Agu 2026: kode membaca
`firstTradeDateEpochUtc`, sedangkan dict `.info` yang benar-benar dipakai
catalyst.py memuat `firstTradeDateMilliseconds` (MILIDETIK). Akibatnya nol
katalis lockup dihasilkan di run 4.273 ticker — gagal diam-diam, bukan error.

Uji unit versi pertama IKUT LOLOS karena memakai dict buatan sendiri dengan
nama field yang dipilih penulisnya. Karena itu tes ini menguji KEDUA nama dan
satuannya, bukan cuma perilaku yang diasumsikan.

Jalankan: python test_catalyst_lockup.py
"""
from __future__ import annotations

import datetime as dt

from montrva.layer2.catalyst import LOCKUP_DAYS, build_catalyst_set

_passed = 0
NOW = dt.datetime(2026, 8, 16, tzinfo=dt.timezone.utc)
TD = NOW.date()
FA = NOW.isoformat()


def check(label, actual, expected):
    global _passed
    if actual != expected:
        raise AssertionError(f"{label}: dapat {actual!r}, harusnya {expected!r}")
    _passed += 1
    print(f"  ok  {label}")


def lockup_of(info):
    cs = build_catalyst_set("T", info, FA, now=NOW)
    hits = [c for c in cs.catalysts if c.catalyst_id.startswith("lockup")]
    return hits[0] if hits else None


def detik(d):
    return int(dt.datetime.combine(d, dt.time(), dt.timezone.utc).timestamp())


def mili(d):
    return detik(d) * 1000


print("1. KEDUA nama field harus bekerja, dengan satuan masing-masing")
ipo = TD - dt.timedelta(days=120)
harap = (ipo + dt.timedelta(days=LOCKUP_DAYS)).isoformat()
check("firstTradeDateEpochUtc (detik)", lockup_of({"firstTradeDateEpochUtc": detik(ipo)}).expected_at, harap)
check("firstTradeDateMilliseconds (milidetik)", lockup_of({"firstTradeDateMilliseconds": mili(ipo)}).expected_at, harap)

print("")
print("2. Jendela horizon")
for hari, harus, ket in [(120, True, "lockup 60 hari lagi"), (200, False, "sudah lewat"),
                         (5, False, "di luar horizon 90 hari"), (16683, False, "perusahaan lama")]:
    ada = lockup_of({"firstTradeDateMilliseconds": mili(TD - dt.timedelta(days=hari))}) is not None
    check("IPO %d hari lalu -> %s" % (hari, ket), ada, harus)

print("")
print("3. Kepastian & bentuk")
c = lockup_of({"firstTradeDateMilliseconds": mili(TD - dt.timedelta(days=120))})
check("certainty = expected", c.certainty, "expected")
check("kind = other", c.kind, "other")

print("")
print("4. Berdampingan dengan katalis lain, tidak saling menggantikan")
cs = build_catalyst_set("T", {"earningsTimestamp": detik(TD + dt.timedelta(days=20)),
                              "firstTradeDateMilliseconds": mili(TD - dt.timedelta(days=120))}, FA, now=NOW)
check("dua katalis", sorted(x.kind for x in cs.catalysts), ["earnings", "other"])
check("has_upcoming", cs.has_upcoming, True)

print("")
print("5. Masukan rusak tidak meledakkan build")
for nilai in (None, "kemarin", "", -1):
    check("firstTradeDateMilliseconds=%r" % (nilai,), lockup_of({"firstTradeDateMilliseconds": nilai}) is None, True)

print("")
print("%d pemeriksaan lolos." % _passed)
