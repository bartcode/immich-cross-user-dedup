import { useCallback, useEffect, useMemo, useState } from "react"
import {
  Album,
  ArrowDown,
  ArrowUp,
  Calendar,
  HardDrive,
  History,
  ImageIcon,
  RotateCcw,
  Sparkles,
  Trash2,
  Undo2,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { api, humanBytes, type JournalDetailDto, type JournalSummaryDto } from "@/lib/api"
import { cn } from "@/lib/utils"

interface UndoPanelProps {
  refreshKey: number
  disabled: boolean
  onStarted: () => void
}

const ASSET_PAGE_SIZE = 20

type ListSortKey = "modified" | "trashed" | "albums" | "merges" | "size"
type AssetSortKey = "name" | "owner" | "bytes"

export function UndoPanel({ refreshKey, disabled, onStarted }: UndoPanelProps) {
  const [journals, setJournals] = useState<JournalSummaryDto[]>([])
  const [listSort, setListSort] = useState<{ key: ListSortKey; asc: boolean }>({ key: "modified", asc: false })
  const [selected, setSelected] = useState<JournalSummaryDto | null>(null)
  const [detail, setDetail] = useState<JournalDetailDto | null>(null)
  const [assetSort, setAssetSort] = useState<{ key: AssetSortKey; asc: boolean }>({ key: "name", asc: true })
  const [assetPage, setAssetPage] = useState(0)
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => {
    api
      .journals()
      .then(setJournals)
      .catch((cause) => setError(String(cause)))
  }, [])

  useEffect(load, [load, refreshKey])

  const sortedJournals = useMemo(() => {
    const { key, asc } = listSort
    const factor = asc ? 1 : -1
    return [...journals].sort((a, b) => {
      const value = (journal: JournalSummaryDto): number | string =>
        key === "modified" ? new Date(journal.modified).getTime()
        : key === "trashed" ? journal.trashed_assets
        : key === "albums" ? journal.album_adds
        : key === "merges" ? journal.metadata_merges
        : journal.size_bytes
      const av = value(a)
      const bv = value(b)
      return (av < bv ? -1 : av > bv ? 1 : 0) * factor
    })
  }, [journals, listSort])

  const sortedAssets = useMemo(() => {
    const assets = detail?.undo_detail?.assets ?? []
    const { key, asc } = assetSort
    const factor = asc ? 1 : -1
    return [...assets].sort((a, b) => {
      const av = key === "bytes" ? a.bytes : key === "owner" ? a.owner_email : a.name
      const bv = key === "bytes" ? b.bytes : key === "owner" ? b.owner_email : b.name
      return (av < bv ? -1 : av > bv ? 1 : 0) * factor
    })
  }, [detail, assetSort])

  const assetPageSlice = sortedAssets.slice(
    assetPage * ASSET_PAGE_SIZE,
    assetPage * ASSET_PAGE_SIZE + ASSET_PAGE_SIZE,
  )
  const assetPageCount = Math.max(1, Math.ceil(sortedAssets.length / ASSET_PAGE_SIZE))

  async function openJournal(journal: JournalSummaryDto) {
    setSelected(journal)
    setAssetPage(0)
    setError(null)
    try {
      setDetail(await api.journal(journal.name))
    } catch (cause) {
      setError(String(cause))
    }
  }

  async function startUndo() {
    if (!selected) return
    try {
      await api.undo(selected.name)
      setConfirmOpen(false)
      onStarted()
    } catch (cause) {
      setError(String(cause))
      setConfirmOpen(false)
    }
  }

  function SortHeader({
    label,
    sortKey,
    align = "left",
  }: {
    label: string
    sortKey: ListSortKey
    align?: "left" | "right"
  }) {
    const active = listSort.key === sortKey
    return (
      <button
        type="button"
        className={cn(
          "flex items-center gap-1 text-xs font-medium hover:text-foreground",
          align === "right" && "justify-end",
          active ? "text-foreground" : "text-muted-foreground",
        )}
        onClick={() => setListSort((prev) => ({ key: sortKey, asc: prev.key === sortKey ? !prev.asc : false }))}
      >
        {label}
        {active && (listSort.asc ? <ArrowUp className="size-3" /> : <ArrowDown className="size-3" />)}
      </button>
    )
  }

  return (
    <div className="grid gap-3">
      <h3 className="flex items-center gap-2 text-sm font-semibold">
        <Undo2 className="size-4 text-rose-500" /> Undo history
      </h3>

      {journals.length === 0 ? (
        <p className="text-sm text-muted-foreground">No apply runs journaled yet.</p>
      ) : (
        <div className="overflow-x-auto rounded-md border">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b bg-muted/50">
                <th className="px-3 py-2 text-left">
                  <span className="flex items-center gap-1 text-xs font-medium text-muted-foreground">
                    <Calendar className="size-3" /> <SortHeader label="Run" sortKey="modified" />
                  </span>
                </th>
                <th className="px-3 py-2 text-right"><SortHeader label="Trashed" sortKey="trashed" align="right" /></th>
                <th className="px-3 py-2 text-right"><SortHeader label="Album adds" sortKey="albums" align="right" /></th>
                <th className="px-3 py-2 text-right"><SortHeader label="Merges" sortKey="merges" align="right" /></th>
                <th className="px-3 py-2 text-right"><SortHeader label="Size" sortKey="size" align="right" /></th>
                <th className="px-3 py-2" />
              </tr>
            </thead>
            <tbody>
              {sortedJournals.map((journal) => (
                <tr
                  key={journal.name}
                  className={cn(
                    "border-b last:border-b-0",
                    selected?.name === journal.name ? "bg-accent" : "hover:bg-muted/40",
                  )}
                >
                  <td className="px-3 py-2">
                    <p className="font-mono text-xs">{new Date(journal.modified).toLocaleString()}</p>
                    <p className="max-w-[220px] truncate text-[11px] text-muted-foreground">{journal.name}</p>
                  </td>
                  <td className="px-3 py-2 text-right">
                    <span className="inline-flex items-center gap-1 text-rose-600 dark:text-rose-400">
                      <Trash2 className="size-3.5" /> {journal.trashed_assets}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-right">
                    <span className="inline-flex items-center gap-1 text-blue-600 dark:text-blue-400">
                      <Album className="size-3.5" /> {journal.album_adds}
                      {journal.album_shares > 0 && (
                        <span className="text-muted-foreground" title="temporary editor shares">
                          (+{journal.album_shares})
                        </span>
                      )}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-right">
                    <span className="inline-flex items-center gap-1 text-fuchsia-600 dark:text-fuchsia-400">
                      <Sparkles className="size-3.5" /> {journal.metadata_merges}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-right text-muted-foreground">
                    <span className="inline-flex items-center gap-1">
                      <HardDrive className="size-3.5" /> {(journal.size_bytes / 1024).toFixed(1)} KiB
                    </span>
                  </td>
                  <td className="px-3 py-2 text-right">
                    <Button size="sm" variant={selected?.name === journal.name ? "secondary" : "outline"} onClick={() => openJournal(journal)}>
                      <History /> Inspect
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {detail && selected && (
        <div className="grid gap-3 rounded-md border bg-muted/30 p-3 text-sm">
          <p className="font-medium">
            Undoing <span className="font-mono text-xs">{selected.name}</span> would:
          </p>

          {sortedAssets.length > 0 && (
            <div>
              <p className="mb-1 text-muted-foreground">
                Restore {sortedAssets.length} trashed asset{sortedAssets.length === 1 ? "" : "s"} (while Immich
                has not purged them yet):
              </p>
              <div className="overflow-x-auto rounded-md border bg-background">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b bg-muted/50">
                      <th className="w-14 px-2 py-1.5" />
                      <th className="px-2 py-1.5 text-left">
                        <AssetSortHeader label="File" sortKey="name" state={assetSort} setState={setAssetSort} onSort={() => setAssetPage(0)} />
                      </th>
                      <th className="px-2 py-1.5 text-left">
                        <AssetSortHeader label="Owner" sortKey="owner" state={assetSort} setState={setAssetSort} onSort={() => setAssetPage(0)} />
                      </th>
                      <th className="px-2 py-1.5 text-right">
                        <AssetSortHeader label="Size" sortKey="bytes" state={assetSort} setState={setAssetSort} onSort={() => setAssetPage(0)} align="right" />
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {assetPageSlice.map((asset) => (
                      <tr key={asset.id} className="border-b last:border-b-0">
                        <td className="px-2 py-1.5">
                          <RestoreThumb id={asset.id} name={asset.name} />
                        </td>
                        <td className="max-w-[280px] truncate px-2 py-1.5">{asset.name}</td>
                        <td className="max-w-[180px] truncate px-2 py-1.5 text-muted-foreground">{asset.owner_email || "unknown"}</td>
                        <td className="px-2 py-1.5 text-right text-muted-foreground">{humanBytes(asset.bytes)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {sortedAssets.length > ASSET_PAGE_SIZE && (
                <div className="mt-1 flex items-center justify-between">
                  <p className="text-xs text-muted-foreground">
                    {assetPage * ASSET_PAGE_SIZE + 1}–{Math.min((assetPage + 1) * ASSET_PAGE_SIZE, sortedAssets.length)} of{" "}
                    {sortedAssets.length}
                  </p>
                  <div className="flex gap-1">
                    <Button variant="outline" size="sm" disabled={assetPage === 0} onClick={() => setAssetPage((page) => page - 1)}>
                      ← Prev
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={assetPage + 1 >= assetPageCount}
                      onClick={() => setAssetPage((page) => page + 1)}
                    >
                      Next →
                    </Button>
                  </div>
                </div>
              )}
            </div>
          )}

          {(detail.undo_detail?.albums ?? []).length > 0 && (
            <div>
              <p className="mb-1 text-muted-foreground">
                Remove the keeper from {detail.undo_detail!.albums.length} album{detail.undo_detail!.albums.length === 1 ? "" : "s"}:
              </p>
              <ul className="grid gap-1 text-xs text-muted-foreground">
                {detail.undo_detail!.albums.map((album, index) => (
                  <li key={index}>
                    “{album.keeper_name ?? "the kept photo"}” leaves “{album.album}” (owned by{" "}
                    {album.owner_email || "another user"})
                  </li>
                ))}
              </ul>
            </div>
          )}

          {(detail.undo_detail?.shares ?? []).length > 0 && (
            <div>
              <p className="mb-1 text-muted-foreground">
                Revoke {detail.undo_detail!.shares.length} temporary editor share{detail.undo_detail!.shares.length === 1 ? "" : "s"}:
              </p>
              <ul className="grid gap-1 text-xs text-muted-foreground">
                {detail.undo_detail!.shares.map((share, index) => (
                  <li key={index}>
                    “{share.album}” (owned by {share.owner_email || "another user"})
                  </li>
                ))}
              </ul>
            </div>
          )}

          {detail.undo_preview.metadata_merges > 0 && (
            <p className="text-muted-foreground">
              Revert {detail.undo_preview.metadata_merges} metadata merge
              {detail.undo_preview.metadata_merges === 1 ? "" : "s"} (favorites/descriptions).
            </p>
          )}

          <p className="flex items-start gap-1.5 text-xs text-muted-foreground">
            <RotateCcw className="mt-0.5 size-3.5 shrink-0" />
            Assets already purged by Immich are reported and skipped — the run continues with the rest.
          </p>
        </div>
      )}

      {error && <p className="text-sm text-destructive">{error}</p>}

      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogTrigger asChild>
          <Button variant="outline" className="justify-self-start" disabled={disabled || !selected}>
            <Undo2 /> Undo selected journal
          </Button>
        </DialogTrigger>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Undo this apply run?</DialogTitle>
            <DialogDescription>
              Restores trashed assets, removes the album additions made by the run and reverts merged
              metadata. Assets already purged by Immich are reported and skipped.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmOpen(false)}>
              Cancel
            </Button>
            <Button variant="destructive" onClick={startUndo}>
              Undo run
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

function AssetSortHeader({
  label,
  sortKey,
  state,
  setState,
  onSort,
  align = "left",
}: {
  label: string
  sortKey: AssetSortKey
  state: { key: AssetSortKey; asc: boolean }
  setState: (value: { key: AssetSortKey; asc: boolean }) => void
  onSort: () => void
  align?: "left" | "right"
}) {
  const active = state.key === sortKey
  return (
    <button
      type="button"
      className={cn(
        "flex items-center gap-1 text-xs font-medium hover:text-foreground",
        align === "right" && "justify-end",
        active ? "text-foreground" : "text-muted-foreground",
      )}
      onClick={() => {
        setState({ key: sortKey, asc: state.key === sortKey ? !state.asc : true })
        onSort()
      }}
    >
      {label}
      {active && (state.asc ? <ArrowUp className="size-3" /> : <ArrowDown className="size-3" />)}
    </button>
  )
}

function RestoreThumb({ id, name }: { id: string; name: string }) {
  const [broken, setBroken] = useState(false)
  return (
    <span className="block size-10 shrink-0 overflow-hidden rounded border bg-muted" title={name}>
      {broken ? (
        <span className="flex size-full items-center justify-center text-muted-foreground">
          <ImageIcon className="size-3.5" />
        </span>
      ) : (
        <img
          src={`/api/thumbnail/${id}`}
          alt={name}
          loading="lazy"
          className="size-full object-cover"
          onError={() => setBroken(true)}
        />
      )}
    </span>
  )
}
