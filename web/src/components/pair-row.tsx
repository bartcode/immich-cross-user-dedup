import { useState } from "react"
import { ExternalLink, Film, ImageIcon, Star } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Switch } from "@/components/ui/switch"
import { humanBytes, type AssetDto, type LoserDto, type PairDto } from "@/lib/api"
import { cn } from "@/lib/utils"

function ownerBadge(email: string): string {
  const local = email.split("@")[0] ?? email
  return local.charAt(0).toUpperCase() + local.slice(1, 7)
}

function AssetThumb({
  asset,
  label,
  badgeClass,
}: {
  asset: AssetDto
  label: string
  badgeClass: string
}) {
  const [broken, setBroken] = useState(false)
  return (
    <a
      href={asset.url}
      target="_blank"
      rel="noreferrer"
      className="group relative block size-20 shrink-0 overflow-hidden rounded-md border bg-muted"
      title={`${label}: ${asset.file_name}`}
    >
      {broken ? (
        <span className="flex size-full flex-col items-center justify-center gap-1 text-muted-foreground">
          {asset.type === "VIDEO" ? <Film className="size-6" /> : <ImageIcon className="size-6" />}
          <span className="max-w-full truncate px-1 text-[10px]">{asset.file_name}</span>
        </span>
      ) : (
        <img
          src={asset.thumbnail_url}
          alt={`${label} ${asset.file_name}`}
          loading="lazy"
          className="size-full object-cover"
          onError={() => setBroken(true)}
        />
      )}
      <span className={cn("absolute top-1 left-1 rounded px-1.5 py-0.5 text-[10px] font-semibold", badgeClass)}>
        {ownerBadge(asset.owner_email)}
      </span>
      {asset.type === "VIDEO" && !broken && (
        <Film className="absolute bottom-1 left-1 size-3.5 text-white drop-shadow" />
      )}
      {asset.is_favorite && (
        <Star className="absolute right-1 bottom-1 size-3.5 fill-yellow-400 text-yellow-400" />
      )}
      <ExternalLink className="absolute right-1 top-1 size-3 opacity-0 transition group-hover:opacity-100" />
    </a>
  )
}

interface PairRowProps {
  pair: PairDto
  onToggle: (pair: PairDto) => void
}

function loserIssues(loser: LoserDto): string[] {
  const issues: string[] = []
  if (loser.live_photo === "keeper-lacks-motion") issues.push("keeper lacks motion")
  if (loser.live_photo === "loser-lacks-motion") issues.push("loser lacks motion")
  return issues
}

export function PairRow({ pair, onToggle }: PairRowProps) {
  const albumNames = pair.losers.flatMap((loser) => loser.albums?.map((album) => album.name) ?? [])
  const issues = pair.losers.flatMap(loserIssues)
  const loserBytes = pair.losers.reduce((sum, loser) => sum + loser.size_bytes, 0)

  return (
    <div className="flex items-center gap-4 border-b px-4 py-3 last:border-b-0">
      <div className="flex max-w-md flex-wrap items-center gap-2">
        <AssetThumb asset={pair.keeper} label="keeper" badgeClass="bg-emerald-600 text-white" />
        <div className="text-xs text-muted-foreground">=</div>
        {pair.losers.map((loser) => (
          <AssetThumb key={loser.id} asset={loser} label="loser" badgeClass="bg-orange-600 text-white" />
        ))}
      </div>

      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="truncate font-medium">{pair.keeper.file_name}</span>
          {pair.keeper.type === "VIDEO" && <Badge variant="outline">video</Badge>}
          {issues.map((issue) => (
            <Badge key={issue} variant="destructive">
              {issue}
            </Badge>
          ))}
          <Badge variant="secondary">
            {pair.losers.length} duplicate{pair.losers.length === 1 ? "" : "s"}
          </Badge>
        </div>
        <div className="mt-0.5 text-xs text-muted-foreground">
          {pair.keeper.taken_at ? new Date(pair.keeper.taken_at).toLocaleString() : "unknown date"} ·{" "}
          {humanBytes(loserBytes)} reclaimable
          {albumNames.length > 0 && <> · in {albumNames.join(", ")}</>}
        </div>
      </div>

      <div className="flex shrink-0 items-center gap-2">
        <span className={cn("text-xs", pair.excluded ? "text-muted-foreground" : "text-foreground")}>
          {pair.excluded ? "excluded" : "dedupe"}
        </span>
        <Switch checked={!pair.excluded} onCheckedChange={() => onToggle(pair)} aria-label="include pair" />
      </div>
    </div>
  )
}
