import { Fragment, useMemo, useState } from 'react'

// columns: [{ key, label, render(row) => node, sortValue(row) => comparable }]
// rows: array of plain objects, each expected to have a `.ticker` field for search.
// renderExpanded (opsional): (row) => node — kalau diisi, klik baris TOGGLE
// expand inline (bukan panggil onRowClick) dan render node ini di baris
// tambahan di bawahnya. expandKey menentukan field kunci unik per row
// (default 'ticker'). Dua prop ini opsional & backward-compatible — 11
// caller lain yang belum butuh expand tetap jalan seperti sebelumnya.
export default function DataTable({
  columns, rows, onRowClick, searchPlaceholder = 'Cari ticker…', renderExpanded, expandKey = 'ticker',
}) {
  const [search, setSearch] = useState('')
  const [sortKey, setSortKey] = useState(null)
  const [sortDir, setSortDir] = useState('asc')
  const [expanded, setExpanded] = useState(null)

  const filtered = useMemo(() => {
    if (!search.trim()) return rows
    const q = search.toUpperCase()
    return rows.filter((r) => String(r.ticker ?? '').toUpperCase().includes(q))
  }, [rows, search])

  const sorted = useMemo(() => {
    if (!sortKey) return filtered
    const col = columns.find((c) => c.key === sortKey)
    if (!col) return filtered
    const getVal = col.sortValue || ((r) => r[sortKey])
    const copy = [...filtered]
    copy.sort((a, b) => {
      const va = getVal(a)
      const vb = getVal(b)
      if (va === null || va === undefined) return 1
      if (vb === null || vb === undefined) return -1
      if (typeof va === 'string') return va.localeCompare(vb)
      return va - vb
    })
    if (sortDir === 'desc') copy.reverse()
    return copy
  }, [filtered, sortKey, sortDir, columns])

  function handleRowClick(row) {
    if (renderExpanded) {
      const key = row[expandKey]
      setExpanded((prev) => (prev === key ? null : key))
      return
    }
    onRowClick?.(row)
  }

  function toggleSort(key) {
    if (sortKey !== key) {
      setSortKey(key)
      setSortDir('asc')
    } else if (sortDir === 'asc') {
      setSortDir('desc')
    } else {
      setSortKey(null)
    }
  }

  return (
    <>
      <div className="controls">
        <input
          className="search"
          placeholder={searchPlaceholder}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              {columns.map((c) => (
                <th key={c.key} onClick={() => toggleSort(c.key)}>
                  {c.label}
                  {sortKey === c.key && (
                    <span className="sort-arrow">{sortDir === 'asc' ? '▲' : '▼'}</span>
                  )}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sorted.length === 0 ? (
              <tr>
                <td className="empty" colSpan={columns.length}>
                  Tidak ada data
                </td>
              </tr>
            ) : (
              sorted.map((row, i) => {
                const key = row[expandKey] ?? row.ticker ?? i
                const isOpen = renderExpanded && expanded === key
                return (
                  <Fragment key={key}>
                    <tr onClick={() => handleRowClick(row)} className={renderExpanded ? 'clickable' : undefined}>
                      {columns.map((c) => (
                        <td key={c.key}>{c.render(row)}</td>
                      ))}
                    </tr>
                    {isOpen && (
                      <tr className="expand-row">
                        <td colSpan={columns.length} style={{ padding: 0 }}>{renderExpanded(row)}</td>
                      </tr>
                    )}
                  </Fragment>
                )
              })
            )}
          </tbody>
        </table>
      </div>
    </>
  )
}
