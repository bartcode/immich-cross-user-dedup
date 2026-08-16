import { useCallback, useEffect, useRef, useState } from "react"
import { RefreshCw, ScanSearch } from "lucide-react"
import { ApplyPanel } from "@/components/apply-panel"
import { PairRow } from "@/components/pair-row"
import { StepsBar, type StepId } from "@/components/steps-bar"
import { UndoPanel } from "@/components/undo-panel"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
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

const PAGE_SIZE = 20

type Filter = "eligible" | "all" | "excluded" | "live-photo"

export default function App() {
  const [config, setConfig] = useState<ConfigDto | null>(null)
  const [configError, setConfigError] = useState<string | null>(null)
  const [job, setJob] = useState<JobStatusResponse["job"] | null>(null)
  const [stats, setStats] = useState<StatsDto | null>(null)
  const [lastResult, setLastResult] = useState<Record<string, unknown> | null>(null)
  const [pairs, setPairs] = useState<PairsResponse | null>(null)
  const [filter, setFilter] = useState<Filter>("eligible")
  const [offset, setOffset] = useState(0)
  const [refreshKey, setRefreshKey] = useState(0)
  const pollRef = useRef<number | null>(null)

  const busy = job?.running ?? false

  const loadPairs = useCallback(() => {
    api
      .pairs(filter, offset, PAGE_SIZE)
      .then(setPairs)
      .catch(() => setPairs(null))
  }, [filter, offset])

  useEffect(() => {
    api.config().then(setConfig).catch((cause) => setConfigError(String(cause)))
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
    pollRef.current = timer
    return () => window.clearInterval(timer)
  }, [busy])

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

  return (
    <div className="mx-auto flex min-h-screen max-w-5xl flex-col gap-6 p-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Immich cross-user dedup</h1>
          {config && (
            <p className="text-sm text-muted-foreground">
              {config.primary_email} <span className="text-xs">(keeps)</span> ← dedup with →{" "}
              {config.secondary_email} <span className="text-xs">(trash)</span> ·{" "}
              <a className="underline underline-offset-2" href={config.immich_url} target="_blank" rel="noreferrer">
                {config.immich_url}
              </a>
            </p>
          )}
        </div>
        {config && (
          <Badge variant={config.partners_bidirectional ? "secondary" : "destructive"}>
            {config.partners_bidirectional ? "partner sharing OK" : "partner sharing missing"}
          </Badge>
        )}
      </header>

      {configError && (
        <Alert variant="destructive">
          <AlertTitle>Backend error</AlertTitle>
          <AlertDescription>{configError}</AlertDescription>
        </Alert>
      )}
      {config && !config.partners_bidirectional && (
        <Alert variant="destructive">
          <AlertTitle>Partner sharing is required</AlertTitle>
          <AlertDescription>
            Album membership transfer across users needs partner sharing enabled in both directions
            (Immich → Account Settings → Partner Sharing).
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
              <span className="text-muted-foreground">
                {job.current}
                {job.total !== null ? ` / ${job.total}` : ""}
              </span>
            </CardTitle>
          </CardHeader>
          <CardContent>
            <Progress value={job.total ? (job.current / job.total) * 100 : undefined} />
          </CardContent>
        </Card>
      )}

      {!busy && lastResult && (
        <Alert>
          <AlertTitle className="capitalize">{String(lastResult.kind)} finished</AlertTitle>
          <AlertDescription>
            <pre className="overflow-x-auto font-mono text-xs whitespace-pre-wrap">
              {typeof lastResult.summary === "string"
                ? lastResult.summary
                : JSON.stringify(lastResult, null, 2)}
            </pre>
          </AlertDescription>
        </Alert>
      )}

      {stats && (
        <section className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
          <StatCard label="Pairs found" value={stats.pair_count} />
          <StatCard label="Eligible" value={stats.eligible_count} />
          <StatCard label="Excluded" value={stats.excluded_count} />
          <StatCard
            label="Live-photo cases"
            value={stats.live_photo_keeper_lacks_motion + stats.live_photo_loser_lacks_motion}
          />
          <StatCard label="Albums affected" value={stats.affected_albums} />
          <StatCard label="Reclaimable" value={humanBytes(stats.reclaimable_bytes)} />
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
          Fetches both users&apos; assets and matches by checksum. Dry run — nothing is changed.
          {stats && (
            <span>
              {" "}
              Last scan: {stats.primary_assets + stats.secondary_assets} assets across both libraries.
            </span>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex-row items-center justify-between space-y-0">
          <CardTitle className="text-base">2 · Review pairs</CardTitle>
          <div className="flex items-center gap-2">
            <Select
              value={filter}
              onValueChange={(value) => {
                setFilter(value as Filter)
                setOffset(0)
              }}
            >
              <SelectTrigger className="w-44">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="eligible">Eligible</SelectItem>
                <SelectItem value="all">All pairs</SelectItem>
                <SelectItem value="excluded">Excluded</SelectItem>
                <SelectItem value="live-photo">Live-photo cases</SelectItem>
              </SelectContent>
            </Select>
            <Button
              variant="outline"
              size="sm"
              disabled={!pairs || offset === 0}
              onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
            >
              Prev
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={!pairs || offset + PAGE_SIZE >= pairs.total}
              onClick={() => setOffset(offset + PAGE_SIZE)}
            >
              Next
            </Button>
          </div>
        </CardHeader>
        <CardContent className="px-0 py-0">
          {!stats && <p className="px-4 py-6 text-sm text-muted-foreground">Run a scan first.</p>}
          {stats && pairs?.items.length === 0 && (
            <p className="px-4 py-6 text-sm text-muted-foreground">
              No pairs for this filter
              {filter === "eligible" && stats.pair_count > 0 ? " — everything excluded." : "."}
            </p>
          )}
          {pairs?.items.map((pair) => (
            <PairRow key={pair.checksum} pair={pair} onToggle={togglePair} />
          ))}
          {pairs && pairs.total > PAGE_SIZE && (
            <p className="px-4 py-2 text-xs text-muted-foreground">
              {offset + 1}–{Math.min(offset + PAGE_SIZE, pairs.total)} of {pairs.total}
            </p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">3 · Apply</CardTitle>
        </CardHeader>
        <CardContent>
          {stats ? (
            <ApplyPanel stats={stats} disabled={busy} onStarted={() => setRefreshKey((key) => key + 1)} />
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
          <UndoPanel refreshKey={refreshKey} disabled={busy} onStarted={() => setRefreshKey((key) => key + 1)} />
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
  if (stats) return stats.pair_count > 0 ? "review" : "done"
  return "scan"
}
