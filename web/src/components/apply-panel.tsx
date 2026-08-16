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
  const trashedEstimate = stats.per_user.reduce((sum, user) => sum + user.trashed_files, 0)

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
    <div className="flex flex-col gap-4">
      <div className="grid gap-4 lg:grid-cols-3">
        <div className="grid gap-1.5">
          <Label htmlFor="limit">Limit (groups)</Label>
          <Input
            id="limit"
            type="number"
            min={1}
            placeholder={`all (${stats.eligible_count})`}
            value={limit}
            onChange={(event) => setLimit(event.target.value)}
          />
          <p className="text-xs text-muted-foreground">
            Process at most this many groups now — leave empty for all. Handy for trying a small batch
            first.
          </p>
        </div>

        <div className="grid gap-1.5">
          <Label>Live photos where the primary lacks the motion clip</Label>
          <Select value={motionPolicy} onValueChange={setMotionPolicy}>
            <SelectTrigger aria-label="Live photo motion policy">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="trash">Trash duplicate + its motion clip</SelectItem>
              <SelectItem value="skip">Skip these groups</SelectItem>
            </SelectContent>
          </Select>
          <p className="text-xs text-muted-foreground">
            A live photo is a still image plus a short motion video, imported as two files. Usually
            every user has both halves. Rarely, the primary&apos;s copy has no motion clip while the
            duplicate does: trashing also removes that clip (the primary keeps the still, just without
            motion playback); skipping leaves those few groups untouched for you to review by hand.
          </p>
        </div>

        <div className="grid gap-1.5">
          <Label htmlFor="merge">Merge favorites &amp; descriptions</Label>
          <div className="flex h-9 items-center gap-2">
            <Switch id="merge" checked={mergeMetadata} onCheckedChange={setMergeMetadata} />
            <span className="text-xs text-muted-foreground">copy them onto the kept photo</span>
          </div>
          <p className="text-xs text-muted-foreground">
            Before a duplicate is trashed: if its owner marked it as favorite or wrote a description
            and the kept copy has neither, both are copied onto the kept copy (and reverted on undo).
            Otherwise that information is lost with the duplicate.
          </p>
        </div>
      </div>

      {error && <p className="text-sm text-destructive">{error}</p>}

      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogTrigger asChild>
          <Button className="justify-self-start" disabled={disabled || stats.eligible_count === 0}>
            <Play /> Apply dedup
          </Button>
        </DialogTrigger>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Apply cross-user dedup?</DialogTitle>
            <DialogDescription>
              This will process <strong>{effectiveCount}</strong> group
              {effectiveCount === 1 ? "" : "s"}:
            </DialogDescription>
          </DialogHeader>
          <ul className="list-disc space-y-1 pl-5 text-sm text-muted-foreground">
            <li>
              The primary user&apos;s copy joins every album that contained another user&apos;s copy
              ({stats.affected_albums} albums affected overall) — album by album, shared with the
              primary as editor where needed.
            </li>
            <li>
              ~{trashedEstimate} duplicate assets from {stats.per_user.length} other user
              {stats.per_user.length === 1 ? "" : "s"} move to the trash — each trashed with that
              user&apos;s own API key (~{humanBytes(stats.reclaimable_bytes)} reclaimable after
              purge). Immich keeps trashed items restorable for the trash retention period.
            </li>
            {mergeMetadata && (
              <li>Favorites and descriptions from duplicates are copied onto the kept copies first.</li>
            )}
            <li>
              Live photos: {motionPolicy === "trash"
                ? "duplicates are trashed together with their motion clips"
                : "groups where the primary lacks the motion clip are left untouched"}
              .
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
