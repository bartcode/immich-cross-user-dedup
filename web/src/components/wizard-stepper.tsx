import { Check, Lock, type LucideIcon } from "lucide-react"
import { cn } from "@/lib/utils"

export interface StepDef {
  id: string
  label: string
  icon: LucideIcon
  /** tailwind classes tinting this step (icon bubble + label) */
  accent: string
  done: boolean
}

interface WizardStepperProps {
  steps: StepDef[]
  current: string
  isReachable: (id: string) => boolean
  onSelect: (id: string) => void
}

export function WizardStepper({ steps, current, isReachable, onSelect }: WizardStepperProps) {
  const currentIndex = steps.findIndex((step) => step.id === current)
  return (
    <nav aria-label="Wizard steps">
      <ol className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:flex lg:items-center lg:gap-1">
        {steps.map((step, index) => {
          const Icon = step.icon
          const isCurrent = step.id === current
          const reachable = isReachable(step.id)
          const state = isCurrent ? "current" : step.done ? "done" : reachable ? "idle" : "locked"
          return (
            <li key={step.id} className="flex items-center gap-1 lg:flex-1">
              <button
                type="button"
                disabled={!reachable || isCurrent}
                onClick={() => onSelect(step.id)}
                className={cn(
                  "flex w-full items-center gap-2.5 rounded-lg border px-3 py-2 text-left text-sm transition",
                  state === "current" && "border-transparent ring-2 ring-primary/60 " + step.accent,
                  state === "done" && "border-emerald-200 bg-emerald-50 dark:border-emerald-500/30 dark:bg-emerald-500/10",
                  state === "idle" && "border-border hover:bg-accent",
                  state === "locked" && "cursor-not-allowed border-dashed opacity-50",
                  !isCurrent && reachable && "cursor-pointer",
                )}
              >
                <span
                  className={cn(
                    "flex size-8 shrink-0 items-center justify-center rounded-full",
                    state === "current" ? step.accent : "bg-muted text-muted-foreground",
                    state === "done" && "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/20 dark:text-emerald-300",
                  )}
                >
                  {state === "done" ? <Check className="size-4" /> : <Icon className="size-4" />}
                </span>
                <span className="min-w-0">
                  <span className="block font-medium leading-tight">{step.label}</span>
                  <span className="block text-[11px] leading-tight text-muted-foreground">
                    Step {index + 1} of {steps.length}
                    {state === "locked" && " · locked"}
                  </span>
                </span>
              </button>
              {index < steps.length - 1 && (
                <span
                  aria-hidden
                  className={cn(
                    "hidden h-0.5 w-4 shrink-0 rounded lg:block",
                    index < currentIndex ? "bg-emerald-400" : "bg-border",
                  )}
                />
              )}
            </li>
          )
        })}
      </ol>
    </nav>
  )
}

export function StepHeader({
  icon: Icon,
  accent,
  title,
  description,
  children,
}: {
  icon: LucideIcon
  accent: string
  title: string
  description?: string
  children?: React.ReactNode
}) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div className="flex items-start gap-3">
        <span className={cn("flex size-10 shrink-0 items-center justify-center rounded-lg", accent)}>
          <Icon className="size-5" />
        </span>
        <div>
          <h2 className="text-lg font-semibold leading-tight">{title}</h2>
          {description && <p className="text-sm text-muted-foreground">{description}</p>}
        </div>
      </div>
      {children}
    </div>
  );
}

export { Lock }
