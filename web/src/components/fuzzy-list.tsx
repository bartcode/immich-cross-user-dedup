import { useState } from "react"
import { AlertTriangle, Loader2, Search } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { api, type FuzzyPairDto } from "@/lib/api"

/** Near-duplicates that differ byte-wise (edits, re-encodes) — report only. */
export function FuzzyList() {
  const [items, setItems] = useState<FuzzyPairDto[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function find() {
    setBusy(true)
    setError(null)
    try {
      const payload = await api.fuzzy()
      setItems(payload.items)
    } catch (cause) {
      setError(String(cause))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card>
      <CardContent className="flex flex-col gap-3 py-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h3 className="flex items-center gap-2 text-sm font-semibold">
              <Search className="size-4 text-fuchsia-500" /> Near-duplicates (different files)
            </h3>
            <p className="text-xs text-muted-foreground">
              Same name, timestamp ±2s, size within 1% — but different bytes. Report only: review these
              by hand; nothing is applied automatically.
            </p>
          </div>
          <Button variant="outline" size="sm" onClick={find} disabled={busy}>
            {busy ? <Loader2 className="animate-spin" /> : <Search />}
            {items === null ? "Find near-duplicates" : "Refresh"}
          </Button>
        </div>

        {error && <p className="text-sm text-destructive">{error}</p>}

        {items !== null && items.length === 0 && (
          <p className="text-sm text-muted-foreground">No near-duplicates found — everything matched byte-for-byte.</p>
        )}

        {items && items.length > 0 && (
          <ul className="grid gap-2 md:grid-cols-2 2xl:grid-cols-3">
            {items.map((pair, index) => (
              <li key={index} className="flex items-center gap-2 rounded-md border p-2">
                <img
                  src={pair.keeper.thumbnail_url}
                  alt={pair.keeper.file_name}
                  loading="lazy"
                  className="size-14 shrink-0 rounded border object-cover"
                />
                <img
                  src={pair.loser.thumbnail_url}
                  alt={pair.loser.file_name}
                  loading="lazy"
                  className="size-14 shrink-0 rounded border object-cover"
                />
                <div className="min-w-0 flex-1 text-xs">
                  <p className="truncate font-medium">{pair.keeper.file_name}</p>
                  <p className="truncate text-muted-foreground">
                    {pair.keeper.owner_email.split("@")[0]} vs {pair.loser.owner_email.split("@")[0]}
                  </p>
                  <p className="flex items-center gap-1 text-muted-foreground">
                    <AlertTriangle className="size-3 text-amber-500" />
                    Δt {pair.time_delta_seconds.toFixed(1)}s
                  </p>
                </div>
                <div className="flex shrink-0 flex-col gap-1">
                  <a
                    href={pair.keeper.url}
                    target="_blank"
                    rel="noreferrer"
                    className="text-[11px] text-emerald-600 underline underline-offset-2 dark:text-emerald-400"
                  >
                    keeper
                  </a>
                  <a
                    href={pair.loser.url}
                    target="_blank"
                    rel="noreferrer"
                    className="text-[11px] text-orange-600 underline underline-offset-2 dark:text-orange-400"
                  >
                    duplicate
                  </a>
                </div>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  )
}
