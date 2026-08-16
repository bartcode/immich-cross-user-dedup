import { Play, ScanSearch } from "lucide-react"
import { ApplyPanel } from "@/components/apply-panel"
import { JobPanel } from "@/components/job-panel"
import { StepHeader } from "@/components/wizard-stepper"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import type { JobDto, StatsDto } from "@/lib/api"

export const APPLY_ACCENT = "bg-emerald-100 text-emerald-600 dark:bg-emerald-500/15 dark:text-emerald-300"

interface ApplyStepProps {
  stats: StatsDto | null
  job: JobDto | null
  busy: boolean
  lastResult: Record<string, unknown> | null
  onJobStarted: () => void
  onNavigate: (id: string) => void
  onError: (message: string) => void
}

export function ApplyStep({ stats, job, busy, lastResult, onJobStarted, onNavigate, onError }: ApplyStepProps) {
  return (
    <div className="grid gap-4">
      <StepHeader
        icon={Play}
        accent={APPLY_ACCENT}
        title="Apply the dedup"
        description="Step 4 — keepers join every album that contained a duplicate; the duplicates move to the trash, journaled and undoable."
      />

      {!stats ? (
        <Card>
          <CardContent className="flex flex-col items-center gap-3 py-10 text-center">
            <span className="flex size-14 items-center justify-center rounded-full bg-emerald-100 text-emerald-500 dark:bg-emerald-500/15">
              <ScanSearch className="size-7" />
            </span>
            <div>
              <p className="font-medium">Scan first</p>
              <p className="text-sm text-muted-foreground">
                Apply works on a scan — run one and review the duplicates before applying.
              </p>
            </div>
            <Button variant="outline" onClick={() => onNavigate("scan")}>
              Go to scan
            </Button>
          </CardContent>
        </Card>
      ) : (
        <>
          <Card>
            <CardContent className="py-4">
              <ApplyPanel stats={stats} disabled={busy} onStarted={onJobStarted} />
            </CardContent>
          </Card>
          <JobPanel kind="apply" job={job} busy={busy} lastResult={lastResult} mode="both" onError={onError} />
        </>
      )}
    </div>
  )
}
