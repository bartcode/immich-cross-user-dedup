import { ArrowRight, Ban, Check, ImageOff, Images, RefreshCw, ScanSearch, Sparkles, Trash2, Users } from "lucide-react"
import { JobPanel } from "@/components/job-panel"
import { StepHeader } from "@/components/wizard-stepper"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { humanBytes, type JobDto, type StatsDto } from "@/lib/api"

export const SCAN_ACCENT = "bg-blue-100 text-blue-600 dark:bg-blue-500/15 dark:text-blue-300"

interface ScanStepProps {
  stats: StatsDto | null
  job: JobDto | null
  busy: boolean
  lastResult: Record<string, unknown> | null
  onScan: () => void
  onNavigate: (id: string) => void
  onError: (message: string) => void
}

export function ScanStep({ stats, job, busy, lastResult, onScan, onNavigate, onError }: ScanStepProps) {
  const scanning = busy && job?.kind === "scan"
  return (
    <div className="grid gap-4">
      <StepHeader
        icon={ScanSearch}
        accent={SCAN_ACCENT}
        title="Scan your libraries"
        description="Step 2 — fetches every user's assets and matches them by checksum. A dry run: nothing is changed."
      >
        <Button onClick={onScan} disabled={busy} size="lg">
          {scanning ? <RefreshCw className="animate-spin" /> : <ScanSearch />}
          {stats ? "Scan again" : "Start scan"}
        </Button>
      </StepHeader>

      <JobPanel kind="scan" job={job} busy={busy} lastResult={lastResult} mode="progress" onError={onError} />

      {!stats && !scanning && (
        <Card>
          <CardContent className="flex flex-col items-center gap-3 py-10 text-center">
            <span className="flex size-14 items-center justify-center rounded-full bg-blue-100 text-blue-500 dark:bg-blue-500/15">
              <Images className="size-7" />
            </span>
            <div>
              <p className="font-medium">No scan yet</p>
              <p className="text-sm text-muted-foreground">
                Start the scan to see how many duplicates exist across your libraries.
              </p>
            </div>
          </CardContent>
        </Card>
      )}

      {stats && (
        <>
          <section className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
            <StatCard icon={Images} tint="text-blue-500" label="Groups found" value={stats.group_count} />
            <StatCard icon={Check} tint="text-emerald-500" label="Eligible" value={stats.eligible_count} />
            <StatCard icon={Ban} tint="text-amber-500" label="Excluded" value={stats.excluded_count} />
            <StatCard
              icon={Sparkles}
              tint="text-fuchsia-500"
              label="Live-photo cases"
              value={stats.live_photo_keeper_lacks_motion + stats.live_photo_loser_lacks_motion}
            />
            <StatCard icon={ImageOff} tint="text-muted-foreground" label="Skipped (no primary)" value={stats.skipped_no_primary} />
            <StatCard icon={Trash2} tint="text-rose-500" label="Reclaimable" value={humanBytes(stats.reclaimable_bytes)} />
          </section>

          {stats.per_user.length > 0 && (
            <section className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              {stats.per_user.map((user) => (
                <Card key={user.email} className="py-3">
                  <CardContent className="flex items-center gap-3 px-4">
                    <span className="flex size-9 shrink-0 items-center justify-center rounded-full bg-orange-100 text-orange-600 dark:bg-orange-500/15 dark:text-orange-300">
                      <Users className="size-4" />
                    </span>
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium">{user.email}</p>
                      <p className="text-xs text-muted-foreground">
                        {user.assets} assets · {user.trashed_files} to trash (
                        {humanBytes(user.trashed_bytes)})
                      </p>
                    </div>
                  </CardContent>
                </Card>
              ))}
            </section>
          )}

          <div className="flex justify-end">
            <Button size="lg" onClick={() => onNavigate("review")} disabled={stats.group_count === 0 && stats.skipped_no_primary === 0}>
              Review duplicates <ArrowRight />
            </Button>
          </div>
        </>
      )}
    </div>
  )
}

function StatCard({
  icon: Icon,
  tint,
  label,
  value,
}: {
  icon: React.ComponentType<{ className?: string }>
  tint: string
  label: string
  value: number | string
}) {
  return (
    <Card className="py-3">
      <CardContent className="flex items-center gap-3 px-4">
        <Icon className={`size-5 shrink-0 ${tint}`} />
        <div className="min-w-0">
          <p className="truncate text-xs text-muted-foreground">{label}</p>
          <p className="text-lg font-semibold leading-tight">{value}</p>
        </div>
      </CardContent>
    </Card>
  )
}
