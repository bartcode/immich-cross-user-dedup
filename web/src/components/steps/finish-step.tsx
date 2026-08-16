import { useEffect, useState } from "react"
import { BadgeCheck, CircleCheckBig, Images, RotateCcw, Undo2 } from "lucide-react"
import { JobPanel } from "@/components/job-panel"
import { UndoPanel } from "@/components/undo-panel"
import { StepHeader } from "@/components/wizard-stepper"
import { Card, CardContent } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { api, type JobDto, type JournalSummaryDto, type StatsDto } from "@/lib/api"

export const FINISH_ACCENT = "bg-rose-100 text-rose-600 dark:bg-rose-500/15 dark:text-rose-300"

interface FinishStepProps {
  job: JobDto | null
  busy: boolean
  lastResult: Record<string, unknown> | null
  stats: StatsDto | null
  refreshKey: number
  onJobStarted: () => void
  onNavigate: (id: string) => void
  onRescan: () => void
  onError: (message: string) => void
}

export function FinishStep({
  job,
  busy,
  lastResult,
  stats,
  refreshKey,
  onJobStarted,
  onNavigate,
  onRescan,
  onError,
}: FinishStepProps) {
  const [journals, setJournals] = useState<JournalSummaryDto[]>([])
  useEffect(() => {
    api.journals().then(setJournals).catch(() => undefined)
  }, [refreshKey])
  const latest = journals[0]
  const ranSomething = lastResult?.kind === "apply" || lastResult?.kind === "undo"
  return (
    <div className="grid gap-4">
      <StepHeader
        icon={BadgeCheck}
        accent={FINISH_ACCENT}
        title={ranSomething ? "Run complete" : "Finish & undo"}
        description="Step 5 — verify the result, undo a run if needed, or re-scan to see what remains."
      />

      {ranSomething ? (
        <>
          <JobPanel kind={lastResult!.kind as "apply" | "undo"} job={job} busy={busy} lastResult={lastResult} mode="result" onError={onError} />
          <div className="flex flex-wrap gap-2">
            <Button onClick={onRescan}>
              <RotateCcw /> Re-scan to verify
            </Button>
            <Button variant="outline" onClick={() => onNavigate("review")}>
              <Undo2 /> Back to review
            </Button>
          </div>
        </>
      ) : (
        <p className="text-sm text-muted-foreground">
          Once you apply (or undo) a run, its results and next steps appear here. Every apply run is
          journaled below and undoable until Immich purges the trash.
        </p>
      )}

      {stats && latest && (
        <Card>
          <CardContent className="flex flex-wrap items-center gap-4 py-4">
            <span className="flex size-10 items-center justify-center rounded-lg bg-emerald-100 text-emerald-600 dark:bg-emerald-500/15 dark:text-emerald-300">
              <CircleCheckBig className="size-5" />
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-sm font-semibold">Re-scan verification</p>
              <p className="text-sm text-muted-foreground">
                The latest run trashed <strong>{latest.trashed_assets}</strong> assets; the re-scan now
                finds <strong>{stats.group_count}</strong> cross-user group
                {stats.group_count === 1 ? "" : "s"}
                {stats.skipped_no_primary > 0 && <> and {stats.skipped_no_primary} group{stats.skipped_no_primary === 1 ? "" : "s"} without a primary copy</>}.
                {stats.group_count === 0
                  ? " Clean — everything eligible was deduplicated."
                  : " Remaining groups are excluded pairs, policy keeps, or need another pass."}
              </p>
            </div>
            <Button variant="outline" onClick={() => onNavigate("review")}>
              <Images /> Review remaining
            </Button>
          </CardContent>
        </Card>
      )}

      <UndoPanel refreshKey={refreshKey} disabled={busy} onStarted={onJobStarted} />
    </div>
  )
}
