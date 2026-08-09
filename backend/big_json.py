"""Akses per-ticker ke dua stage file terbesar tanpa menahan isinya di RAM.

Kenapa ada: `backend/app.py::_warm_cache()` memuat SELURUH 14 stage file ke
`_stage_cache` saat startup dan menahannya selama backend hidup. Diukur
2026-08-08: ~970 MB JSON di disk yang mengembang jadi 3,32 GB objek Python --
44% dari RAM mesin ini (7,47 GB), dengan committed sistem 9,21 GB sehingga
Windows terus menumpahkan ke pagefile. Dua file menyumbang 87% dari angka itu:
`historical_timeline.json` (598 MB) dan `evidence.json` (250 MB).

Yang sebenarnya dibaca jauh lebih kecil dari yang ditahan:

  - `/api/ticker/<t>` butuh SATU ticker dari 4.054
  - `/api/evidence/summary` dan `/api/historical/summary` butuh semua baris
    tapi cuma sebagian kecil field -- keduanya memang sudah membuang array
    besarnya sebelum mengirim
  - `/api/evidence` & `/api/historical` mentah tidak dipakai frontend sama
    sekali (`api.js` mendefinisikannya, tidak ada view yang memanggil)

Tidak ada satu pun yang butuh seluruh file tinggal di memori antar-request.

Modul ini menggantinya dengan indeks offset byte: satu pemindaian mencatat
posisi tiap record, lalu tiap pembacaan `seek` + `json.loads` satu potong
(~11-60 KB). Indeksnya sendiri ~50 byte per ticker (~0,4 MB untuk kedua file).

## Kenapa pemindaiannya berbasis penanda, bukan parser JSON penuh

Pilihan pertama yang wajar -- state machine yang melacak kedalaman kurung
sambil sadar string/escape -- tidak terpakai karena biayanya di Python: JSON
sepadat ini punya satu karakter struktural tiap ~8 byte, jadi 598 MB berarti
puluhan juta iterasi level-Python. Itu MENIT, jauh lebih lambat dari
`json.load` yang mau digantikan.

Yang dipakai: `bytes.find()` atas satu penanda yang unik per record. Itu
C-speed dan cuma melompat ~4.000 kali untuk seluruh file. Konsekuensinya
penanda itu spesifik-format dan bisa meleset kalau bentuk file berubah --
karena itu `build_*_index()` SELALU memvalidasi hasilnya (jumlah record +
parse contoh acak) dan mengembalikan None kalau gagal, supaya pemanggil jatuh
ke `json.load` biasa. Salah tebak berarti lambat, tidak pernah berarti salah
data.

Puncak memori pemindaian sebesar chunk (4 MB), bukan sebesar file.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

CHUNK = 4 << 20

# Awal record di dalam array "packages" evidence.json. Tiap paket ditulis
# `dump_safe` dari dataclass EvidencePackage yang field pertamanya `ticker`,
# jadi batas antar-record selalu byte-persis begini pada file indent=None.
_EVIDENCE_BOUNDARY = b'},{"ticker":"'
_EVIDENCE_ARRAY = b'"packages":['

# `total_entries` adalah field level-timeline di historical_timeline.json dan
# TIDAK muncul di dalam entries[] (kunci entry cuma entry_id/analyzed_at/
# aggregator_output/method_versions/outcome) -- jadi tepat satu kemunculan per
# ticker, dan itu yang menjadikannya penanda record yang sah.
_HISTORICAL_MARKER = b'"total_entries":'

_TICKER_IN_RECORD = re.compile(rb'"ticker"\s*:\s*"([^"]{1,15})"')
_KEY_BEFORE_BRACE = re.compile(rb'"([^"]{1,15})"\s*:\s*$')

# Berapa record yang di-parse ulang untuk membuktikan indeksnya benar sebelum
# dipakai. Disebar rata, bukan diambil dari depan saja -- kesalahan penanda
# khasnya muncul di tengah file (record dengan bentuk tak terduga), bukan di
# record pertama.
_VALIDATE_SAMPLES = 8


class RecordIndex:
    """Peta ticker -> (offset awal, offset akhir) di dalam satu berkas JSON.

    `mtime` disimpan supaya pemanggil bisa membuang indeks yang sudah basi
    dengan aturan yang sama seperti `_stage_cache` di app.py: satu run pipeline
    menulis ulang file ini, dan indeks lama akan menunjuk offset yang salah.
    """

    __slots__ = ("path", "mtime", "spans")

    def __init__(self, path: Path, mtime: float, spans: dict[str, tuple[int, int]]):
        self.path = path
        self.mtime = mtime
        self.spans = spans

    def __len__(self) -> int:
        return len(self.spans)

    def __contains__(self, ticker: str) -> bool:
        return ticker in self.spans

    def get(self, ticker: str) -> dict | None:
        """Satu record, dibaca langsung dari disk. None kalau tickernya tidak
        ada di indeks -- BUKAN dibedakan dari 'ada tapi isinya null', karena
        kedua file ini tidak pernah menyimpan null di level record."""
        span = self.spans.get(ticker)
        if span is None:
            return None
        return self._read(span)

    def _read(self, span: tuple[int, int]) -> dict:
        start, end = span
        with open(self.path, "rb") as fh:
            fh.seek(start)
            raw = fh.read(end - start)
        return json.loads(raw.decode("utf-8"))

    def iter_records(self):
        """Semua record satu per satu. Dipakai endpoint ringkasan: puncak
        memorinya satu record, bukan seluruh file -- itulah bedanya dengan
        `json.load` yang menahan 4.054 record sekaligus supaya bisa membuang
        99% field-nya."""
        with open(self.path, "rb") as fh:
            for ticker, (start, end) in self.spans.items():
                fh.seek(start)
                yield ticker, json.loads(fh.read(end - start).decode("utf-8"))


def _find_all(path: Path, marker: bytes, start: int = 0) -> list[int]:
    """Offset tiap kemunculan `marker`, dipindai berchunk.

    Chunk bertumpang tindih `len(marker) - 1` byte supaya penanda yang
    terpotong di batas chunk tetap ketemu. Tumpang tindih itu tidak bisa
    menghasilkan duplikat: kecocokan yang dilaporkan pada iterasi sebelumnya
    pasti berawal di posisi <= akhir - len(marker), sedangkan carry dimulai di
    akhir - len(marker) + 1.
    """
    overlap = len(marker) - 1
    offsets: list[int] = []
    with open(path, "rb") as fh:
        fh.seek(start)
        carry = b""
        carry_base = start
        pos = start
        while True:
            buf = fh.read(CHUNK)
            if not buf:
                break
            hay = carry + buf
            i = hay.find(marker)
            while i != -1:
                offsets.append(carry_base + i)
                i = hay.find(marker, i + 1)
            pos += len(buf)
            if overlap:
                carry = hay[-overlap:]
                carry_base = pos - len(carry)
            else:
                carry_base = pos
    return offsets


def _last_byte_of(path: Path, char: bytes) -> int:
    """Offset kemunculan TERAKHIR `char`. Dipakai mencari kurung penutup
    top-level tanpa asumsi soal newline/spasi di ekor berkas."""
    size = path.stat().st_size
    window = min(size, 4096)
    with open(path, "rb") as fh:
        fh.seek(size - window)
        tail = fh.read(window)
    i = tail.rfind(char)
    return -1 if i == -1 else size - window + i


def _read_at(path: Path, start: int, length: int) -> bytes:
    with open(path, "rb") as fh:
        fh.seek(start)
        return fh.read(length)


_SESSION_ID = re.compile(rb'"session_id"\s*:\s*"([^"]*)"')


def read_session_id(path: Path) -> str | None:
    """`session_id` dari kepala berkas, tanpa mem-parse isinya.

    Ada karena /api/consistency cuma butuh satu field per stage file, tapi
    mengambilnya lewat `_get_stage()` berarti json.load 250 MB -- satu
    permintaan ke endpoint itu mengembalikan seluruh memori yang baru saja
    dihemat _BIG_STAGES, dan frontend memanggilnya di tiap muat halaman.

    `dump_safe` menulis field wrapper sebelum array `packages`, jadi 8 KB
    pertama selalu memuatnya. Kalau tidak ketemu di situ -> None, sama seperti
    berkas lama yang memang belum punya field ini (endpoint-nya sudah
    memperlakukan None sebagai "tidak diketahui", bukan error).
    """
    try:
        head = _read_at(path, 0, 8192)
    except OSError:
        return None
    m = _SESSION_ID.search(head)
    return m.group(1).decode("utf-8") if m else None


def _validate(index: RecordIndex, expected: int | None) -> bool:
    """Buktikan indeksnya sebelum dipakai: jumlah record cocok (kalau berkasnya
    menyebutkan jumlah) dan contoh yang disebar rata benar-benar parse jadi
    objek dengan ticker yang sesuai."""
    if not index.spans:
        return False
    if expected is not None and len(index.spans) != expected:
        return False

    tickers = list(index.spans)
    step = max(1, len(tickers) // _VALIDATE_SAMPLES)
    for ticker in tickers[::step][:_VALIDATE_SAMPLES]:
        try:
            rec = index.get(ticker)
        except (ValueError, UnicodeDecodeError, OSError):
            return False
        if not isinstance(rec, dict):
            return False
        # historical_timeline.json memakai ticker sebagai KUNCI dan juga
        # menyimpannya di dalam record; evidence.json cuma di dalam record.
        # Keduanya harus cocok -- kalau tidak, offsetnya bergeser.
        if rec.get("ticker") != ticker:
            return False
    return True


def build_evidence_index(path: Path) -> RecordIndex | None:
    """Indeks `evidence.json` -- objek top-level dengan array `packages`."""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None

    head = _read_at(path, 0, 4096)
    array_at = head.find(_EVIDENCE_ARRAY)
    if array_at == -1:
        return None
    first_start = array_at + len(_EVIDENCE_ARRAY)

    # Array kosong -> indeks kosong yang sah, tapi _validate menolak spans
    # kosong, jadi tangani di sini supaya tidak dikira gagal pindai.
    if head[first_start:first_start + 1] == b"]":
        return RecordIndex(path, mtime, {})

    boundaries = _find_all(path, _EVIDENCE_BOUNDARY, first_start)
    last_end = _last_byte_of(path, b"]")
    if last_end == -1:
        return None

    starts = [first_start] + [b + 2 for b in boundaries]
    ends = [b + 1 for b in boundaries] + [last_end]
    if len(starts) != len(ends):
        return None

    spans: dict[str, tuple[int, int]] = {}
    for start, end in zip(starts, ends):
        if end <= start:
            return None
        m = _TICKER_IN_RECORD.search(_read_at(path, start, 200))
        if m is None:
            return None
        spans[m.group(1).decode("utf-8")] = (start, end)

    expected = None
    try:
        expected = json.loads(head[:head.index(b'"packages"')].rstrip()[:-1] + b"}").get("evidence_generated")
    except (ValueError, IndexError):
        expected = None

    index = RecordIndex(path, mtime, spans)
    return index if _validate(index, expected) else None


def build_historical_index(path: Path) -> RecordIndex | None:
    """Indeks `historical_timeline.json` -- objek top-level berkunci ticker."""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None

    markers = _find_all(path, _HISTORICAL_MARKER)
    if not markers:
        return None

    # Dari penanda, mundur ke `{` pembuka record. Antara `{` dan
    # `"total_entries"` cuma ada `"ticker":"XXX",` -- tidak ada kurung
    # bersarang -- jadi `{` terdekat sebelum penanda pasti awal record.
    starts: list[int] = []
    keys: list[str] = []
    for marker_at in markers:
        back_from = max(0, marker_at - 64)
        chunk = _read_at(path, back_from, marker_at - back_from)
        brace = chunk.rfind(b"{")
        if brace == -1:
            return None
        value_start = back_from + brace

        key_chunk = _read_at(path, max(0, value_start - 24), min(24, value_start))
        m = _KEY_BEFORE_BRACE.search(key_chunk)
        if m is None:
            return None
        starts.append(value_start)
        keys.append(m.group(1).decode("utf-8"))

    close_at = _last_byte_of(path, b"}")
    if close_at == -1:
        return None

    # Record berakhir tepat sebelum koma pemisah yang mendahului kunci
    # berikutnya; record terakhir berakhir sebelum `}` penutup top-level.
    ends: list[int] = []
    for i, start in enumerate(starts):
        if i + 1 < len(starts):
            next_key_quote = starts[i + 1] - 1
            key_len = len(keys[i + 1].encode("utf-8"))
            ends.append(next_key_quote - key_len - 3)
        else:
            ends.append(close_at)

    spans: dict[str, tuple[int, int]] = {}
    for key, start, end in zip(keys, starts, ends):
        if end <= start:
            return None
        spans[key] = (start, end)

    index = RecordIndex(path, mtime, spans)
    return index if _validate(index, len(markers)) else None
