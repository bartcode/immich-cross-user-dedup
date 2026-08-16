import { useCallback, useEffect, useState } from "react"
import { Info, RefreshCw, ScanSearch, Settings, Square } from "lucide-react"
import { ApplyPanel } from "@/components/apply-panel"
import { ConnectionForm } from "@/components/connection-form"
import { PairRow } from "@/components/pair-row"
import { StepsBar, type StepId } from "@/components/steps-bar"
import { UndoPanel } from "@/components/undo-panel"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Progress } from "@/components/ui/progress"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import {
  api,
  humanBytes,
  type ConfigDto,
  type JobStatusResponse,
  type PairDto,
  type PairsResponse,
  type StatsDto,
} from "@/lib/api"

const PAGE_SIZE = 15

/** Page numbers with ellipsis trimming: 1 … (current-1) current (current+1) … N */
function pageList(current: number, total: number): (number | "…")[] {
  if (total <= 7) {
    return Array.from({ length: total }, (_, index) => index + 1)
  }
  const wanted = [1, 2, current - 1, current, current + 1, total - 1, total].filter(
    (page) => page >= 1 && page <= total,
  )
  const unique = [...new Set(wanted)].sort((a, b) => a - b)
  const result: (number | "…")[] = []
  let previous = 0
  for (const page of unique) {
    if (page - previous > 1) result.push("…")
    result.push(page)
    previous = page
  }
  return result
}

type Filter = "eligible" | "all" | "excluded" | "live-photo"
type Sort = "date-desc" | "date-asc" | "size-desc" | "size-asc"

export default function App() {
  const [config, setConfig] = useState<ConfigDto | null>(null)
  const [configError, setConfigError] = useState<string | null>(null)
  const [job, setJob] = useState<JobStatusResponse["job"] | null>(null)
  const [stats, setStats] = useState<StatsDto | null>(null)
  const [lastResult, setLastResult] = useState<Record<string, unknown> | null>(null)
  const [pairs, setPairs] = useState<PairsResponse | null>(null)
  const [filter, setFilter] = useState<Filter>("eligible")
  const [sort, setSort] = useState<Sort>("date-desc")
  const [offset, setOffset] = useState(0)
  const [refreshKey, setRefreshKey] = useState(0)
  const [settingsOpen, setSettingsOpen] = useState(false)

  const busy = job?.running ?? false

  const reloadConfig = useCallback(() => {
    api
      .config()
      .then((payload) => {
        setConfig(payload)
        setJob(null)
        setStats(null)
        setLastResult(null)
        setSettingsOpen(false)
        setRefreshKey((key) => key + 1)
      })
      .catch((cause) => setConfigError(String(cause)))
  }, [])

  const loadPairs = useCallback(() => {
    api
      .pairs(filter, offset, PAGE_SIZE, sort)
      .then(setPairs)
      .catch(() => setPairs(null))
  }, [filter, offset, sort])

  useEffect(() => {
    api.config().then(setConfig).catch((cause) => setConfigError(String(cause)))
    // restore stats / last result after a page refresh (the backend keeps the scan)
    api
      .job()
      .then((payload) => {
        setJob(payload.job)
        setStats(payload.stats)
        setLastResult(payload.last_result)
      })
      .catch(() => undefined)
  }, [])

  useEffect(loadPairs, [loadPairs, refreshKey])

  // poll the job while one is running; bump refreshKey when it finishes
  useEffect(() => {
    if (!busy) return
    const timer = window.setInterval(() => {
      api
        .job()
        .then((payload) => {
          setJob(payload.job)
          setStats(payload.stats)
          setLastResult(payload.last_result)
          if (!payload.job.running) {
            setRefreshKey((key) => key + 1)
          }
        })
        .catch(() => undefined)
    }, 1000)
    return () => window.clearInterval(timer)
  }, [busy])

  // called by the apply/undo panels right after they started a job: pick up the
  // running state so the progress card appears and polling begins (small jobs
  // may already be finished — then just refresh the data)
  const handleJobStarted = useCallback(() => {
    api
      .job()
      .then((payload) => {
        setJob(payload.job)
        if (!payload.job.running) {
          setLastResult(payload.last_result)
          setRefreshKey((key) => key + 1)
        }
      })
      .catch(() => undefined)
  }, [])

  async function startScan() {
    try {
      await api.scan()
      const payload = await api.job()
      setJob(payload.job)
    } catch (cause) {
      setConfigError(String(cause))
    }
  }

  async function togglePair(pair: PairDto) {
    const action = pair.excluded ? api.include : api.exclude
    await action(pair.checksum)
    loadPairs()
    api.stats().then(setStats).catch(() => undefined)
  }

  const step = deriveStep(job, stats, lastResult)

  const currentPage = Math.floor(offset / PAGE_SIZE) + 1
  const totalPages = pairs ? Math.max(1, Math.ceil(pairs.total / PAGE_SIZE)) : 1

  const goToPage = (page: number) => setOffset((page - 1) * PAGE_SIZE)

  const pager = (
    <div className="flex flex-wrap items-center gap-1">
      <Button
        variant="outline"
        size="sm"
        disabled={!pairs || offset === 0}
        onClick={() => goToPage(currentPage - 1)}
      >
        ← Prev
      </Button>
      {pairs &&
        pageList(currentPage, totalPages).map((page, index) =>
          page === "…" ? (
            <span key={`ellipsis-${index}`} className="px-1 text-xs text-muted-foreground">
              …
            </span>
          ) : (
            <Button
              key={page}
              variant={page === currentPage ? "default" : "outline"}
              size="sm"
              className="min-w-8"
              aria-label={`Go to page ${page}`}
              aria-current={page === currentPage ? "page" : undefined}
              onClick={() => goToPage(page)}
            >
              {page}
            </Button>
          ),
        )}
      <Button
        variant="outline"
        size="sm"
        disabled={!pairs || offset + PAGE_SIZE >= pairs.total}
        onClick={() => goToPage(currentPage + 1)}
      >
        Next →
      </Button>
    </div>
  )

  const reviewControls = (
    <div className="flex flex-wrap items-center gap-2">
      <Select
        value={filter}
        onValueChange={(value) => {
          setFilter(value as Filter)
          setOffset(0)
        }}
      >
        <SelectTrigger className="w-44" aria-label="Filter groups">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="eligible">Eligible</SelectItem>
          <SelectItem value="all">All groups</SelectItem>
          <SelectItem value="excluded">Excluded</SelectItem>
          <SelectItem value="live-photo">Live-photo cases</SelectItem>
        </SelectContent>
      </Select>
      <Select
        value={sort}
        onValueChange={(value) => {
          setSort(value as Sort)
          setOffset(0)
        }}
      >
        <SelectTrigger className="w-44" aria-label="Sort groups">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="date-desc">Newest first</SelectItem>
          <SelectItem value="date-asc">Oldest first</SelectItem>
          <SelectItem value="size-desc">Largest first</SelectItem>
          <SelectItem value="size-asc">Smallest first</SelectItem>
        </SelectContent>
      </Select>
      {pager}
    </div>
  )

  if (config && !config.configured) {
    return (
      <div className="mx-auto flex min-h-screen w-full max-w-[1800px] flex-col gap-6 p-4 sm:p-6">
        <h1 className="text-2xl font-semibold tracking-tight">Immich cross-user dedup</h1>
        {configError && (
          <Alert variant="destructive">
            <AlertTitle>Backend error</AlertTitle>
            <AlertDescription>{configError}</AlertDescription>
          </Alert>
        )}
        <ConnectionForm current={config} onConfigured={reloadConfig} />
      </div>
    )
  }

  return (
    <div className="mx-auto flex min-h-screen w-full max-w-[1800px] flex-col gap-6 p-4 sm:p-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Immich cross-user dedup</h1>
          {config && (
            <p className="text-sm text-muted-foreground">
              {config.primary_email} <span className="text-xs">(keeps)</span> ← dedup with →{" "}
              {config.secondaries.map((s) => s.email).join(", ")}{" "}
              <span className="text-xs">(trash)</span> ·{" "}
              <a className="underline underline-offset-2" href={config.immich_url} target="_blank" rel="noreferrer">
                {config.immich_url}
              </a>
            </p>
          )}
        </div>
        <div className="flex items-center gap-2">
          {config && (
            <Badge variant={config.partners_ok ? "secondary" : "outline"}>
              {config.partners_ok ? "partner sharing: direct transfers" : "album-editor sharing"}
            </Badge>
          )}
          <Dialog open={settingsOpen} onOpenChange={setSettingsOpen}>
            <DialogTrigger asChild>
              <Button variant="outline" size="sm" disabled={busy}>
                <Settings /> Connection
              </Button>
            </DialogTrigger>
            <DialogContent className="max-h-[85vh] max-w-xl overflow-y-auto">
              <DialogHeader>
                <DialogTitle>Connection settings</DialogTitle>
                <DialogDescription>
                  Changing these resets the current session (scan results and exclusions).
                </DialogDescription>
              </DialogHeader>
              <ConnectionForm current={config} onConfigured={reloadConfig} />
            </DialogContent>
          </Dialog>
        </div>
      </header>

      {configError && (
        <Alert variant="destructive">
          <AlertTitle>Backend error</AlertTitle>
          <AlertDescription>{configError}</AlertDescription>
        </Alert>
      )}
      {config && !config.partners_ok && (
        <Alert>
          <Info className="size-4" />
          <AlertTitle>Partner sharing is not enabled — that&apos;s fine</AlertTitle>
          <AlertDescription>
            Affected albums will be shared with the primary as <strong>editor</strong> during apply
            (revoked on undo), so nobody gets access to anyone&apos;s full library. Enable partner
            sharing in Immich only if the secondaries should also see the primary&apos;s entire
            timeline.
          </AlertDescription>
        </Alert>
      )}
      {job?.error && (
        <Alert variant="destructive">
          <AlertTitle>Last job failed</AlertTitle>
          <AlertDescription className="font-mono text-xs">{job.error}</AlertDescription>
        </Alert>
      )}

      <StepsBar current={step} active={busy} />

      {busy && job && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="flex items-center justify-between text-sm font-medium">
              <span className="capitalize">
                {job.kind} · {job.stage}
              </span>
              <span className="flex items-center gap-3">
                <span className="text-muted-foreground">
                  {job.current}
                  {job.total !== null ? ` / ${job.total}` : ""}
                </span>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => api.cancelJob().catch((cause) => setConfigError(String(cause)))}
                >
                  <Square /> Stop
                </Button>
              </span>
            </CardTitle>
          </CardHeader>
          <CardContent>
            <Progress value={job.total ? (job.current / job.total) * 100 : undefined} />
          </CardContent>
        </Card>
      )}

      {!busy && lastResult && (
        <Alert variant={lastResult.error_count || lastResult.aborted ? "destructive" : "default"}>
          <AlertTitle>
            {lastResult.headline ? (
              <>
                {String(lastResult.headline)}
                {lastResult.error_count ? (
                  <> — {Number(lastResult.error_count)} error{Number(lastResult.error_count) === 1 ? "" : "s"}</>
                ) : null}
              </>
            ) : (
              <span className="capitalize">
                {String(lastResult.kind)} {lastResult.cancelled ? "cancelled" : "finished"}
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
                        <li key={index} className="font-mono">{String(sample)}</li>
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

      {stats && (
        <section className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
          <StatCard label="Groups found" value={stats.group_count} />
          <StatCard label="Eligible" value={stats.eligible_count} />
          <StatCard label="Excluded" value={stats.excluded_count} />
          <StatCard
            label="Live-photo cases"
            value={stats.live_photo_keeper_lacks_motion + stats.live_photo_loser_lacks_motion}
          />
          <StatCard label="Skipped (no primary copy)" value={stats.skipped_no_primary} />
          <StatCard label="Reclaimable" value={humanBytes(stats.reclaimable_bytes)} />
        </section>
      )}

      {stats && stats.per_user.length > 0 && (
        <section className="grid gap-3 sm:grid-cols-3">
          {stats.per_user.map((userStats) => (
            <Card key={userStats.email} className="py-3">
              <CardContent className="px-3">
                <p className="truncate text-xs text-muted-foreground">{userStats.email}</p>
                <p className="text-sm">
                  {userStats.assets} assets · {userStats.trashed_files} to trash (
                  {humanBytes(userStats.trashed_bytes)})
                </p>
              </CardContent>
            </Card>
          ))}
        </section>
      )}

      <Card>
        <CardHeader className="flex-row items-center justify-between space-y-0">
          <CardTitle className="text-base">1 · Scan</CardTitle>
          <Button onClick={startScan} disabled={busy}>
            {busy && job?.kind === "scan" ? <RefreshCw className="animate-spin" /> : <ScanSearch />}
            Scan libraries
          </Button>
        </CardHeader>
        <CardContent className="text-sm text-muted-foreground">
          Fetches every user&apos;s assets and matches by checksum. Dry run — nothing is changed.
          {stats && (
            <span>
              {" "}
              Last scan: {stats.primary_assets} primary +{" "}
              {stats.per_user.reduce((sum, user) => sum + user.assets, 0)} secondary assets.
            </span>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex-row flex-wrap items-center justify-between gap-2 space-y-0">
          <CardTitle className="text-base">2 · Review groups</CardTitle>
          {reviewControls}
        </CardHeader>
        <CardContent className="px-0 py-0">
          {!stats && <p className="px-4 py-6 text-sm text-muted-foreground">Run a scan first.</p>}
          {stats && pairs?.items.length === 0 && (
            <p className="px-4 py-6 text-sm text-muted-foreground">
              No groups for this filter
              {filter === "eligible" && stats.group_count > 0 ? " — everything excluded." : "."}
            </p>
          )}
          {pairs && pairs.items.length > 0 && (
            <div className="grid grid-cols-1 gap-3 px-4 py-3 md:grid-cols-2 2xl:grid-cols-3">
              {pairs.items.map((pair) => (
                <PairRow key={pair.checksum} pair={pair} onToggle={togglePair} />
              ))}
            </div>
          )}
          {pairs && pairs.total > PAGE_SIZE && (
            <div className="flex flex-wrap items-center justify-between gap-2 border-t px-4 py-2">
              <p className="text-xs text-muted-foreground">
                {offset + 1}–{Math.min(offset + PAGE_SIZE, pairs.total)} of {pairs.total}
              </p>
              {pager}
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">3 · Apply</CardTitle>
        </CardHeader>
        <CardContent>
          {stats ? (
            <ApplyPanel stats={stats} disabled={busy} onStarted={handleJobStarted} />
          ) : (
            <p className="text-sm text-muted-foreground">Run a scan first.</p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">4 · Undo</CardTitle>
        </CardHeader>
        <CardContent>
          <UndoPanel refreshKey={refreshKey} disabled={busy} onStarted={handleJobStarted} />
        </CardContent>
      </Card>

      <footer className="pb-6 text-center text-xs text-muted-foreground">
        immich-cross-user-dedup · dry-run first, apply in small batches, undo until purge
      </footer>
    </div>
  )
}

function StatCard({ label, value }: { label: string; value: number | string }) {
  return (
    <Card className="py-3">
      <CardContent className="px-3">
        <p className="text-xs text-muted-foreground">{label}</p>
        <p className="text-lg font-semibold">{value}</p>
      </CardContent>
    </Card>
  )
}

function deriveStep(
  job: JobStatusResponse["job"] | null,
  stats: StatsDto | null,
  lastResult: Record<string, unknown> | null,
): StepId {
  if (job?.running) {
    if (job.kind === "scan") return "scan"
    if (job.kind === "apply") return "apply"
    if (job.kind === "undo") return "done"
  }
  if (lastResult?.kind === "apply" || lastResult?.kind === "undo") return "done"
  if (stats) return stats.group_count > 0 ? "review" : "done"
  return "scan"
}
