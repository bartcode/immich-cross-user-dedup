import { useState } from "react"
import { Play } from "lucide-react"
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
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { api, humanBytes, type StatsDto } from "@/lib/api"

interface ApplyPanelProps {
  stats: StatsDto
  disabled: boolean
  onStarted: () => void
}

export function ApplyPanel({ stats, disabled, onStarted }: ApplyPanelProps) {
  const [limit, setLimit] = useState("")
  const [mergeMetadata, setMergeMetadata] = useState(false)
  const [motionPolicy, setMotionPolicy] = useState("trash")
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const effectiveCount = limit ? Math.min(Number(limit), stats.eligible_count) : stats.eligible_count

  async function startApply() {
    try {
      await api.apply({
        merge_metadata: mergeMetadata,
        live_photo_motion: motionPolicy,
        limit: limit ? Number(limit) : null,
      })
      setConfirmOpen(false)
      onStarted()
    } catch (cause) {
      setError(String(cause))
      setConfirmOpen(false)
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="grid gap-3 sm:grid-cols-3">
        <div className="grid gap-1.5">
          <Label htmlFor="limit">Limit (pairs)</Label>
          <Input
            id="limit"
            type="number"
            min={1}
            placeholder={`all (${stats.eligible_count})`}
            value={limit}
            onChange={(event) => setLimit(event.target.value)}
          />
        </div>
        <div className="grid gap-1.5">
          <Label>Live photos without keeper motion</Label>
          <Select value={motionPolicy} onValueChange={setMotionPolicy}>
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="trash">Trash loser + its motion</SelectItem>
              <SelectItem value="skip">Skip these pairs</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor="merge">Merge favorites &amp; descriptions</Label>
          <div className="flex h-9 items-center gap-2">
            <Switch id="merge" checked={mergeMetadata} onCheckedChange={setMergeMetadata} />
            <span className="text-xs text-muted-foreground">onto the keeper</span>
          </div>
        </div>
      </div>

      {error && <p className="text-sm text-destructive">{error}</p>}

      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogTrigger asChild>
          <Button disabled={disabled || stats.eligible_count === 0}>
            <Play /> Apply dedup
          </Button>
        </DialogTrigger>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Apply cross-user dedup?</DialogTitle>
            <DialogDescription>
              This will process <strong>{effectiveCount}</strong> pair
              {effectiveCount === 1 ? "" : "s"}:
            </DialogDescription>
          </DialogHeader>
          <ul className="list-disc space-y-1 pl-5 text-sm text-muted-foreground">
            <li>
              The primary user&apos;s copy joins every album that contained the secondary user&apos;s
              copy ({stats.affected_albums} albums affected overall).
            </li>
            <li>
              The secondary user&apos;s copies move to the trash (~
              {humanBytes(stats.reclaimable_bytes)} reclaimable after purge).
            </li>
            <li>
              Live photos: {motionPolicy === "trash" ? "loser still + motion trashed together" : "asymmetric pairs skipped"}
              {mergeMetadata ? "; favorites/descriptions merged onto keepers" : ""}.
            </li>
            <li>Everything is journaled and reversible via Undo until Immich purges the trash.</li>
          </ul>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmOpen(false)}>
              Cancel
            </Button>
            <Button onClick={startApply}>Yes, apply</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
