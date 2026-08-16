import { Link2 } from "lucide-react"
import { ConnectionForm } from "@/components/connection-form"
import { StepHeader } from "@/components/wizard-stepper"
import type { ConfigDto } from "@/lib/api"

export const CONNECT_ACCENT = "bg-violet-100 text-violet-600 dark:bg-violet-500/15 dark:text-violet-300"

export function ConnectStep({
  config,
  onConfigured,
}: {
  config: ConfigDto | null
  onConfigured: () => void
}) {
  return (
    <div className="grid gap-4">
      <StepHeader
        icon={Link2}
        accent={CONNECT_ACCENT}
        title="Connect to your Immich server"
        description="Step 1 — who keeps the photos, and whose duplicates should go."
      />
      <ConnectionForm current={config} onConfigured={onConfigured} />
    </div>
  )
}
