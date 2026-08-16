import { Check, Loader2 } from "lucide-react"
import { cn } from "@/lib/utils"

export type StepId = "scan" | "review" | "apply" | "done"

const STEPS: { id: StepId; label: string; hint: string }[] = [
  { id: "scan", label: "Scan", hint: "match by checksum" },
  { id: "review", label: "Review", hint: "exclude pairs" },
  { id: "apply", label: "Apply", hint: "albums + trash" },
  { id: "done", label: "Done", hint: "undo available" },
]

interface StepsBarProps {
  current: StepId
  active: boolean
}

export function StepsBar({ current, active }: StepsBarProps) {
  const currentIndex = STEPS.findIndex((s) => s.id === current)
  return (
    <ol className="flex flex-wrap items-center gap-2">
      {STEPS.map((step, index) => {
        const state =
          index < currentIndex ? "complete" : index === currentIndex ? (active ? "active" : "current") : "todo"
        return (
          <li key={step.id} className="flex items-center gap-2">
            <div
              className={cn(
                "flex items-center gap-2 rounded-full border px-3 py-1.5 text-sm",
                state === "complete" && "border-primary bg-primary text-primary-foreground",
                state === "current" && "border-primary text-foreground",
                state === "active" && "border-primary bg-accent text-accent-foreground",
                state === "todo" && "border-border text-muted-foreground",
              )}
            >
              <span className="flex size-4 items-center justify-center">
                {state === "complete" ? (
                  <Check className="size-3.5" />
                ) : state === "active" ? (
                  <Loader2 className="size-3.5 animate-spin" />
                ) : (
                  <span className="text-xs font-medium">{index + 1}</span>
                )}
              </span>
              <span className="font-medium">{step.label}</span>
              <span className="hidden text-xs opacity-70 sm:inline">{step.hint}</span>
            </div>
            {index < STEPS.length - 1 && <div className="h-px w-6 bg-border" />}
          </li>
        )
      })}
    </ol>
  )
}
