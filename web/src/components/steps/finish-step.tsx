import { BadgeCheck, RotateCcw, Undo2 } from "lucide-react"
import { JobPanel } from "@/components/job-panel"
import { UndoPanel } from "@/components/undo-panel"
import { StepHeader } from "@/components/wizard-stepper"
import { Button } from "@/components/ui/button"
import type { JobDto } from "@/lib/api"

export const FINISH_ACCENT = "bg-rose-100 text-rose-600 dark:bg-rose-500/15 dark:text-rose-300"

interface FinishStepProps {
  job: JobDto | null
  busy: boolean
  lastResult: Record<string, unknown> | null
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
  refreshKey,
  onJobStarted,
  onNavigate,
  onRescan,
  onError,
}: FinishStepProps) {
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

      <UndoPanel refreshKey={refreshKey} disabled={busy} onStarted={onJobStarted} />
    </div>
  )
}
