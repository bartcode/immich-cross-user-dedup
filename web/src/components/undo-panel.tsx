import { useCallback, useEffect, useState } from "react"
import { History, ImageIcon, RotateCcw, Undo2 } from "lucide-react"
import { Badge } from "@/components/ui/badge"
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

interface UndoPanelProps {
  refreshKey: number
  disabled: boolean
  onStarted: () => void
}

const MAX_SHOWN_ASSETS = 60

function RestoreThumb({ id, name }: { id: string; name: string }) {
  const [broken, setBroken] = useState(false)
  return (
    <span className="block size-12 shrink-0 overflow-hidden rounded border bg-muted" title={name}>
      {broken ? (
        <span className="flex size-full items-center justify-center text-muted-foreground">
          <ImageIcon className="size-4" />
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

export function UndoPanel({ refreshKey, disabled, onStarted }: UndoPanelProps) {
  const [journals, setJournals] = useState<JournalSummaryDto[]>([])
  const [selected, setSelected] = useState<JournalSummaryDto | null>(null)
  const [detail, setDetail] = useState<JournalDetailDto | null>(null)
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => {
    api
      .journals()
      .then(setJournals)
      .catch((cause) => setError(String(cause)))
  }, [])

  useEffect(load, [load, refreshKey])

  async function openJournal(journal: JournalSummaryDto) {
    setSelected(journal)
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

  const assets = detail?.undo_detail?.assets ?? []
  const albums = detail?.undo_detail?.albums ?? []
  const shares = detail?.undo_detail?.shares ?? []
  const shownAssets = assets.slice(0, MAX_SHOWN_ASSETS)

  return (
    <div className="flex flex-col gap-3">
      {journals.length === 0 ? (
        <p className="text-sm text-muted-foreground">No apply runs journaled yet.</p>
      ) : (
        <ul className="divide-y rounded-md border">
          {journals.map((journal) => (
            <li key={journal.name} className="flex items-center justify-between gap-2 px-3 py-2">
              <div className="min-w-0">
                <p className="truncate font-mono text-sm">{journal.name}</p>
                <p className="text-xs text-muted-foreground">
                  {new Date(journal.modified).toLocaleString()} · {(journal.size_bytes / 1024).toFixed(1)} KiB
                </p>
              </div>
              <div className="flex items-center gap-2">
                {selected?.name === journal.name && detail && (
                  <div className="hidden gap-1 sm:flex">
                    <Badge variant="secondary">{detail.undo_preview.trashed_assets} trashed</Badge>
                    <Badge variant="secondary">{detail.undo_preview.album_adds} album adds</Badge>
                    <Badge variant="secondary">{detail.undo_preview.metadata_merges} merges</Badge>
                  </div>
                )}
                <Button size="sm" variant="outline" onClick={() => openJournal(journal)}>
                  <History /> Inspect
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}

      {detail && selected && (
        <div className="grid gap-3 rounded-md border bg-muted/40 p-3 text-sm">
          <p className="font-medium">
            Undoing <span className="font-mono text-xs">{selected.name}</span> would:
          </p>

          {assets.length > 0 && (
            <div>
              <p className="mb-1 text-muted-foreground">
                Restore {assets.length} trashed asset{assets.length === 1 ? "" : "s"} from the trash
                (as long as Immich has not purged them yet):
              </p>
              <ul className="grid gap-1.5">
                {shownAssets.map((asset) => (
                  <li key={asset.id} className="flex items-center gap-2">
                    <RestoreThumb id={asset.id} name={asset.name} />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate">{asset.name}</span>
                      <span className="block text-xs text-muted-foreground">
                        {asset.owner_email || "unknown owner"} · {humanBytes(asset.bytes)}
                      </span>
                    </span>
                  </li>
                ))}
              </ul>
              {assets.length > shownAssets.length && (
                <p className="mt-1 text-xs text-muted-foreground">
                  + {assets.length - shownAssets.length} more…
                </p>
              )}
            </div>
          )}

          {albums.length > 0 && (
            <div>
              <p className="mb-1 text-muted-foreground">Remove the keeper from {albums.length} album{albums.length === 1 ? "" : "s"}:</p>
              <ul className="grid gap-1 text-xs text-muted-foreground">
                {albums.map((album, index) => (
                  <li key={index}>
                    “{album.keeper_name ?? "the kept photo"}” leaves “{album.album}” (owned by{" "}
                    {album.owner_email || "another user"})
                  </li>
                ))}
              </ul>
            </div>
          )}

          {shares.length > 0 && (
            <div>
              <p className="mb-1 text-muted-foreground">
                Revoke {shares.length} temporary editor share{shares.length === 1 ? "" : "s"}:
              </p>
              <ul className="grid gap-1 text-xs text-muted-foreground">
                {shares.map((share, index) => (
                  <li key={index}>
                    “{share.album}”{share.owner_email ? ` (owned by ${share.owner_email})` : ""}
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
            Assets already purged by Immich are reported and skipped — the run continues with the
            rest.
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
