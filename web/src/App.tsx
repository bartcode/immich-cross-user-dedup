import { useCallback, useEffect, useRef, useState } from "react"
import { BadgeCheck, Images, Info, Link2, Play, ScanSearch, Settings } from "lucide-react"
import { ApplyStep } from "@/components/steps/apply-step"
import { ConnectStep } from "@/components/steps/connect-step"
import { FinishStep } from "@/components/steps/finish-step"
import { ReviewStep } from "@/components/steps/review-step"
import { ScanStep } from "@/components/steps/scan-step"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { ConnectionForm } from "@/components/connection-form"
import { WizardStepper, type StepDef } from "@/components/wizard-stepper"
import {
  api,
  type ConfigDto,
  type JobDto,
  type StatsDto,
} from "@/lib/api"

type StepId = "connect" | "scan" | "review" | "apply" | "finish"

const STEPS: { id: StepId; label: string; icon: typeof Link2; accent: string }[] = [
  { id: "connect", label: "Connect", icon: Link2, accent: "bg-violet-100 text-violet-600 dark:bg-violet-500/15 dark:text-violet-300" },
  { id: "scan", label: "Scan", icon: ScanSearch, accent: "bg-blue-100 text-blue-600 dark:bg-blue-500/15 dark:text-blue-300" },
  { id: "review", label: "Review", icon: Images, accent: "bg-amber-100 text-amber-600 dark:bg-amber-500/15 dark:text-amber-300" },
  { id: "apply", label: "Apply", icon: Play, accent: "bg-emerald-100 text-emerald-600 dark:bg-emerald-500/15 dark:text-emerald-300" },
  { id: "finish", label: "Finish", icon: BadgeCheck, accent: "bg-rose-100 text-rose-600 dark:bg-rose-500/15 dark:text-rose-300" },
]

export default function App() {
  const [config, setConfig] = useState<ConfigDto | null>(null)
  const [configError, setConfigError] = useState<string | null>(null)
  const [job, setJob] = useState<JobDto | null>(null)
  const [stats, setStats] = useState<StatsDto | null>(null)
  const [lastResult, setLastResult] = useState<Record<string, unknown> | null>(null)
  const [refreshKey, setRefreshKey] = useState(0)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [hasJournals, setHasJournals] = useState(false)
  const [step, setStep] = useState<StepId>("scan")

  const busy = job?.running ?? false

  // --- data loading ---------------------------------------------------------

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
    api
      .journals()
      .then((journals) => setHasJournals(journals.length > 0))
      .catch(() => undefined)
  }, [])

  // unconfigured → connection step; connected → leave the connection step
  useEffect(() => {
    if (config && !config.configured) setStep("connect")
  }, [config?.configured])

  // poll the job while one is running
  useEffect(() => {
    if (!busy) return
    const timer = window.setInterval(() => {
      api
        .job()
        .then((payload) => {
          setJob(payload.job)
          setStats(payload.stats)
          setLastResult(payload.last_result)
          if (!payload.job.running) setRefreshKey((key) => key + 1)
        })
        .catch(() => undefined)
    }, 1000)
    return () => window.clearInterval(timer)
  }, [busy])

  // called by the apply/undo panels right after they started a job
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

  // auto-advance when a job finishes
  const previousRun = useRef<{ kind: string | null; running: boolean } | null>(null)
  useEffect(() => {
    const current = { kind: job?.kind ?? null, running: busy }
    const previous = previousRun.current
    previousRun.current = current
    if (!previous?.running || current.running || !current.kind || job?.error) return
    if (current.kind === "scan") setStep("review")
    else setStep("finish") // apply or undo
  }, [busy, job?.kind, job?.error])

  // --- actions --------------------------------------------------------------

  async function startScan() {
    try {
      await api.scan()
      const payload = await api.job()
      setJob(payload.job)
    } catch (cause) {
      setConfigError(String(cause))
    }
  }

  const refreshStats = useCallback(() => {
    api.stats().then(setStats).catch(() => undefined)
  }, [])

  const updateJournalsPresence = useCallback(() => {
    api
      .journals()
      .then((journals) => setHasJournals(journals.length > 0))
      .catch(() => undefined)
  }, [])

  useEffect(updateJournalsPresence, [updateJournalsPresence, refreshKey])

  const navigate = useCallback((id: string) => setStep(id as StepId), [])

  // --- wizard gating ----------------------------------------------------------

  const isReachable = useCallback(
    (id: string): boolean => {
      switch (id as StepId) {
        case "connect":
          return true
        case "scan":
          return !!config?.configured
        case "review":
        case "apply":
          return !!stats
        case "finish":
          return !!lastResult || hasJournals
      }
    },
    [config?.configured, stats, lastResult, hasJournals],
  )

  const stepDefs: StepDef[] = STEPS.map((entry) => ({
    ...entry,
    done:
      (entry.id === "scan" && !!stats) ||
      (entry.id === "review" && (lastResult?.kind === "apply" || lastResult?.kind === "undo")) ||
      (entry.id === "apply" && lastResult?.kind === "apply") ||
      (entry.id === "connect" && !!config?.configured),
  }))

  const currentIndex = STEPS.findIndex((entry) => entry.id === step)
  const nextStep = currentIndex < STEPS.length - 1 ? STEPS[currentIndex + 1] : null
  const prevStep = currentIndex > 0 ? STEPS[currentIndex - 1] : null

  // --- render ----------------------------------------------------------------

  return (
    <div className="mx-auto flex min-h-screen w-full max-w-[1800px] flex-col gap-6 p-4 sm:p-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="bg-gradient-to-r from-violet-500 via-blue-500 to-emerald-500 bg-clip-text text-2xl font-semibold tracking-tight text-transparent dark:from-violet-400 dark:via-blue-400 dark:to-emerald-400">
            Immich cross-user dedup
          </h1>
          {config?.configured && (
            <p className="text-sm text-muted-foreground">
              {config.primary_email} <span className="text-xs">(keeps)</span> ← dedup with →{" "}
              {config.secondaries.map((secondary) => secondary.email).join(", ")}{" "}
              <span className="text-xs">(trash)</span> ·{" "}
              <a className="underline underline-offset-2" href={config.immich_url} target="_blank" rel="noreferrer">
                {config.immich_url}
              </a>
            </p>
          )}
        </div>
        <div className="flex items-center gap-2">
          {config?.configured && (
            <Badge variant={config.partners_ok ? "secondary" : "outline"}>
              {config.partners_ok ? "partner sharing: direct transfers" : "album-editor sharing"}
            </Badge>
          )}
          {config?.configured && (
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
          )}
        </div>
      </header>

      {configError && (
        <Alert variant="destructive">
          <AlertTitle>Backend error</AlertTitle>
          <AlertDescription>{configError}</AlertDescription>
        </Alert>
      )}
      {config?.configured && !config.partners_ok && (
        <Alert>
          <Info className="size-4" />
          <AlertTitle>Partner sharing is not enabled — that&apos;s fine</AlertTitle>
          <AlertDescription>
            Affected albums will be shared with the primary as <strong>editor</strong> during apply
            (revoked on undo), so nobody gets access to anyone&apos;s full library. Enable partner
            sharing in Immich only if the secondaries should also see the primary&apos;s entire timeline.
          </AlertDescription>
        </Alert>
      )}
      {job?.error && (
        <Alert variant="destructive">
          <AlertTitle>Last job failed</AlertTitle>
          <AlertDescription className="font-mono text-xs">{job.error}</AlertDescription>
        </Alert>
      )}

      <WizardStepper steps={stepDefs} current={step} isReachable={isReachable} onSelect={navigate} />

      <main className="flex-1">
        {step === "connect" && <ConnectStep config={config} onConfigured={reloadConfig} />}
        {step === "scan" && (
          <ScanStep
            stats={stats}
            job={job}
            busy={busy}
            lastResult={lastResult}
            onScan={startScan}
            onNavigate={navigate}
            onError={setConfigError}
          />
        )}
        {step === "review" && (
          <ReviewStep stats={stats} refreshKey={refreshKey} onNavigate={navigate} onStatsRefresh={refreshStats} />
        )}
        {step === "apply" && (
          <ApplyStep
            stats={stats}
            job={job}
            busy={busy}
            lastResult={lastResult}
            onJobStarted={handleJobStarted}
            onNavigate={navigate}
            onError={setConfigError}
          />
        )}
        {step === "finish" && (
          <FinishStep
            job={job}
            busy={busy}
            lastResult={lastResult}
            refreshKey={refreshKey}
            onJobStarted={handleJobStarted}
            onNavigate={navigate}
            onRescan={() => {
              navigate("scan")
              startScan()
            }}
            onError={setConfigError}
          />
        )}
      </main>

      <footer className="flex flex-wrap items-center justify-between gap-2 border-t pt-3 pb-4">
        <Button variant="ghost" size="sm" disabled={!prevStep || !isReachable(prevStep.id)} onClick={() => prevStep && navigate(prevStep.id)}>
          ← {prevStep?.label ?? "Back"}
        </Button>
        <p className="text-xs text-muted-foreground">
          immich-cross-user-dedup · dry-run first, apply in small batches, undo until purge
        </p>
        <Button
          variant="outline"
          size="sm"
          disabled={!nextStep || !isReachable(nextStep.id) || busy}
          onClick={() => nextStep && navigate(nextStep.id)}
        >
          {nextStep?.label ?? "Done"} →
        </Button>
      </footer>
    </div>
  )
}
