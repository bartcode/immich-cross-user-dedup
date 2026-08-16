import { useCallback, useEffect, useState } from "react"
import { AlignJustify, ArrowRight, Ban, CircleCheckBig, Images, LayoutGrid, ListChecks, X } from "lucide-react"
import { FuzzyList } from "@/components/fuzzy-list"
import { PairRow } from "@/components/pair-row"
import { StepHeader } from "@/components/wizard-stepper"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { api, type PairDto, type PairsResponse, type StatsDto } from "@/lib/api"
import { useLocalStorage } from "@/lib/use-local-storage"

export const REVIEW_ACCENT = "bg-amber-100 text-amber-600 dark:bg-amber-500/15 dark:text-amber-300"

const PAGE_SIZE = 15

type Filter = "eligible" | "all" | "excluded" | "live-photo"
type Sort = "date-desc" | "date-asc" | "size-desc" | "size-asc"
type Density = "comfortable" | "compact"

/** Page numbers with ellipsis trimming: 1 … (current-1) current (current+1) … N */
function pageList(current: number, total: number): (number | "…")[] {
  if (total <= 7) return Array.from({ length: total }, (_, index) => index + 1)
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

interface ReviewStepProps {
  stats: StatsDto | null
  refreshKey: number
  onNavigate: (id: string) => void
  onStatsRefresh: () => void
}

export function ReviewStep({ stats, refreshKey, onNavigate, onStatsRefresh }: ReviewStepProps) {
  const [pairs, setPairs] = useState<PairsResponse | null>(null)
  const [filter, setFilter] = useLocalStorage<Filter>("dedup.filter", "eligible")
  const [sort, setSort] = useLocalStorage<Sort>("dedup.sort", "date-desc")
  const [density, setDensity] = useLocalStorage<Density>("dedup.density", "comfortable")
  const [offset, setOffset] = useState(0)
  const [selected, setSelected] = useState<Set<string>>(new Set())

  const loadPairs = useCallback(() => {
    api
      .pairs(filter, offset, PAGE_SIZE, sort)
      .then(setPairs)
      .catch(() => setPairs(null))
  }, [filter, offset, sort])

  useEffect(loadPairs, [loadPairs, refreshKey])

  async function togglePair(pair: PairDto) {
    const action = pair.excluded ? api.include : api.exclude
    await action(pair.checksum)
    loadPairs()
    onStatsRefresh()
  }

  async function bulk(action: "exclude" | "include") {
    const checksums = [...selected]
    if (checksums.length === 0) return
    await api.bulkPairs(action, checksums)
    setSelected(new Set())
    loadPairs()
    onStatsRefresh()
  }

  function toggleSelected(checksum: string, nowSelected: boolean) {
    setSelected((previous) => {
      const next = new Set(previous)
      if (nowSelected) next.add(checksum)
      else next.delete(checksum)
      return next
    })
  }

  const pageChecksums = pairs?.items.map((pair) => pair.checksum) ?? []
  const allOnPageSelected = pageChecksums.length > 0 && pageChecksums.every((checksum) => selected.has(checksum))

  function toggleSelectPage() {
    setSelected((previous) => {
      const next = new Set(previous)
      if (allOnPageSelected) pageChecksums.forEach((checksum) => next.delete(checksum))
      else pageChecksums.forEach((checksum) => next.add(checksum))
      return next
    })
  }

  const currentPage = Math.floor(offset / PAGE_SIZE) + 1
  const totalPages = pairs ? Math.max(1, Math.ceil(pairs.total / PAGE_SIZE)) : 1
  const goToPage = (page: number) => setOffset((page - 1) * PAGE_SIZE)

  const pager = (
    <div className="flex flex-wrap items-center gap-1">
      <Button variant="outline" size="sm" disabled={!pairs || offset === 0} onClick={() => goToPage(currentPage - 1)}>
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

  const compact = density === "compact"

  return (
    <div className="grid gap-4">
      <StepHeader
        icon={Images}
        accent={REVIEW_ACCENT}
        title="Review the duplicates"
        description="Step 3 — green keeps its file, orange gets trashed. Switch off anything that should stay in both libraries."
      >
        <Button
          variant="outline"
          size="sm"
          aria-label="Toggle thumbnail density"
          onClick={() => setDensity(compact ? "comfortable" : "compact")}
        >
          {compact ? <AlignJustify /> : <LayoutGrid />}
          {compact ? "Compact" : "Comfortable"}
        </Button>
      </StepHeader>

      {!stats ? (
        <EmptyState onNavigate={() => onNavigate("scan")} />
      ) : (
        <>
          {stats.excluded_count > 0 && (
            <p className="flex items-center gap-1.5 text-sm text-muted-foreground">
              <Ban className="size-4 text-amber-500" />
              {stats.excluded_count} group{stats.excluded_count === 1 ? "" : "s"} excluded — they stay duplicated on
              purpose.
            </p>
          )}

          {selected.size > 0 && (
            <div className="flex flex-wrap items-center gap-2 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-sm dark:border-amber-500/40 dark:bg-amber-500/10">
              <span className="flex items-center gap-1.5 font-medium">
                <ListChecks className="size-4 text-amber-600" />
                {selected.size} selected
              </span>
              <span className="text-muted-foreground">(kept across pages)</span>
              <div className="ml-auto flex flex-wrap gap-2">
                <Button size="sm" variant="outline" onClick={() => bulk("include")}>
                  <CircleCheckBig /> Include
                </Button>
                <Button size="sm" onClick={() => bulk("exclude")}>
                  <Ban /> Exclude
                </Button>
                <Button size="sm" variant="ghost" onClick={() => setSelected(new Set())}>
                  <X /> Clear
                </Button>
              </div>
            </div>
          )}

          <Card>
            <div className="flex flex-wrap items-center justify-between gap-2 border-b px-4 py-3">
              <div className="flex flex-wrap items-center gap-2">
                <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
                  <input
                    type="checkbox"
                    aria-label="Select all groups on this page"
                    checked={allOnPageSelected}
                    onChange={toggleSelectPage}
                    className="size-4 accent-amber-500"
                  />
                  page
                </label>
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
              </div>
              {pager}
            </div>
            <CardContent className="px-0 py-0">
              {pairs?.items.length === 0 && (
                <p className="px-4 py-6 text-sm text-muted-foreground">
                  No groups for this filter
                  {filter === "eligible" && stats.group_count > 0 ? " — everything excluded." : "."}
                </p>
              )}
              {pairs && pairs.items.length > 0 && (
                <div
                  className={`grid grid-cols-1 gap-3 px-4 py-3 md:grid-cols-2 ${
                    compact ? "xl:grid-cols-3 2xl:grid-cols-4" : "2xl:grid-cols-3"
                  }`}
                >
                  {pairs.items.map((pair) => (
                    <PairRow
                      key={pair.checksum}
                      pair={pair}
                      onToggle={togglePair}
                      selected={selected.has(pair.checksum)}
                      onSelect={toggleSelected}
                      compact={compact}
                    />
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

          <FuzzyList />

          <div className="flex justify-end">
            <Button size="lg" onClick={() => onNavigate("apply")}>
              Continue to apply <ArrowRight />
            </Button>
          </div>
        </>
      )}
    </div>
  )
}

function EmptyState({ onNavigate }: { onNavigate: () => void }) {
  return (
    <Card>
      <CardContent className="flex flex-col items-center gap-3 py-10 text-center">
        <span className="flex size-14 items-center justify-center rounded-full bg-amber-100 text-amber-500 dark:bg-amber-500/15">
          <Images className="size-7" />
        </span>
        <div>
          <p className="font-medium">Nothing to review yet</p>
          <p className="text-sm text-muted-foreground">Run a scan first — the duplicates will show up here.</p>
        </div>
        <Button onClick={onNavigate} variant="outline">
          Go to scan
        </Button>
      </CardContent>
    </Card>
  )
}
