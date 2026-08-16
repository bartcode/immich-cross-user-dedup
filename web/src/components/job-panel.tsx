import { Square } from "lucide-react"
import { api, type JobDto } from "@/lib/api"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Progress } from "@/components/ui/progress"

export type JobKind = "scan" | "apply" | "undo"

interface JobPanelProps {
  kind: JobKind
  job: JobDto | null
  busy: boolean
  lastResult: Record<string, unknown> | null
  /** progress bar while running, result alert when done, or both */
  mode?: "progress" | "result" | "both"
  onError?: (message: string) => void
}

export function JobPanel({ kind, job, busy, lastResult, mode = "both", onError }: JobPanelProps) {
  const showProgress = (mode === "progress" || mode === "both") && busy && job?.kind === kind
  const showError = (mode === "result" || mode === "both") && !busy && job?.kind === kind && job.error
  const showResult =
    (mode === "result" || mode === "both") && !busy && !job?.error && lastResult?.kind === kind

  return (
    <div className="grid gap-3">
      {showProgress && job && (
        <div className="rounded-md border p-3">
          <div className="mb-2 flex items-center justify-between text-sm font-medium">
            <span className="capitalize">{job.stage}</span>
            <span className="flex items-center gap-3">
              <span className="text-muted-foreground">
                {job.current}
                {job.total !== null ? ` / ${job.total}` : ""}
              </span>
              <Button
                variant="outline"
                size="sm"
                onClick={() => api.cancelJob().catch((cause) => onError?.(String(cause)))}
              >
                <Square /> Stop
              </Button>
            </span>
          </div>
          <Progress value={job.total ? (job.current / job.total) * 100 : undefined} />
        </div>
      )}

      {showError && job && (
        <Alert variant="destructive">
          <AlertTitle className="capitalize">{kind} failed</AlertTitle>
          <AlertDescription className="font-mono text-xs">{job.error}</AlertDescription>
        </Alert>
      )}

      {showResult && lastResult && (
        <Alert variant={lastResult.error_count || lastResult.aborted ? "destructive" : "default"}>
          <AlertTitle>
            {lastResult.headline ? (
              <>
                {String(lastResult.headline)}
                {lastResult.error_count ? (
                  <>
                    {" "}
                    — {Number(lastResult.error_count)} error
                    {Number(lastResult.error_count) === 1 ? "" : "s"}
                  </>
                ) : null}
              </>
            ) : (
              <span className="capitalize">
                {kind} {lastResult.cancelled ? "cancelled" : "finished"}
              </span>
            )}
          </AlertTitle>
          <AlertDescription>
            {lastResult.cancelled ? (
              <>{String(lastResult.note ?? "")}</>
            ) : (
              <>
                {typeof lastResult.summary === "string" && (
                  <pre className="overflow-x-auto font-mono text-xs whitespace-pre-wrap">
                    {lastResult.summary}
                  </pre>
                )}
                {typeof lastResult.summary !== "string" &&
                  !Array.isArray(lastResult.error_samples) && (
                    <pre className="overflow-x-auto font-mono text-xs whitespace-pre-wrap">
                      {JSON.stringify(lastResult, null, 2)}
                    </pre>
                  )}
                {Array.isArray(lastResult.error_samples) && lastResult.error_samples.length > 0 && (
                  <div className="mt-1">
                    <p className="text-xs font-semibold">First errors:</p>
                    <ul className="list-disc pl-4 text-xs">
                      {lastResult.error_samples.map((sample, index) => (
                        <li key={index} className="font-mono">
                          {String(sample)}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {lastResult.album_failure_reasons &&
                  Object.keys(lastResult.album_failure_reasons).length > 0 && (
                    <div className="mt-1">
                      <p className="text-xs font-semibold">Album failures by reason:</p>
                      <ul className="list-disc pl-4 text-xs">
                        {Object.entries(lastResult.album_failure_reasons).map(([reason, count]) => (
                          <li key={reason} className="font-mono">
                            {String(count)}× {reason}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                {lastResult.blocked_owners && Object.keys(lastResult.blocked_owners).length > 0 && (
                  <div className="mt-1">
                    <p className="text-xs font-semibold">Blocked users (their copies were kept):</p>
                    <ul className="list-disc pl-4 text-xs">
                      {Object.entries(lastResult.blocked_owners).map(([owner, reason]) => (
                        <li key={owner}>
                          <span className="font-semibold">{owner}</span> — {String(reason)}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {typeof lastResult.note === "string" && lastResult.note && (
                  <p className="mt-1 font-sans text-xs">{lastResult.note}</p>
                )}
              </>
            )}
          </AlertDescription>
        </Alert>
      )}
    </div>
  )
}
