import { useCallback, useEffect, useState } from "react"
import { History, Undo2 } from "lucide-react"
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
import { api, type JournalDetailDto, type JournalSummaryDto } from "@/lib/api"

interface UndoPanelProps {
  refreshKey: number
  disabled: boolean
  onStarted: () => void
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
        <div className="rounded-md border bg-muted/40 p-3 text-sm">
          <p className="mb-1 font-medium">Selected: {selected.name}</p>
          <p className="text-muted-foreground">
            Undo would restore {detail.undo_preview.trashed_assets} asset
            {detail.undo_preview.trashed_assets === 1 ? "" : "s"}, remove{" "}
            {detail.undo_preview.album_adds} album addition
            {detail.undo_preview.album_adds === 1 ? "" : "s"} and revert{" "}
            {detail.undo_preview.metadata_merges} metadata merge
            {detail.undo_preview.metadata_merges === 1 ? "" : "s"} — as long as Immich has not purged
            the trash yet.
          </p>
        </div>
      )}

      {error && <p className="text-sm text-destructive">{error}</p>}

      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogTrigger asChild>
          <Button variant="outline" disabled={disabled || !selected}>
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
