import { ExternalLink, Star } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Switch } from "@/components/ui/switch"
import { humanBytes, type AssetDto, type PairDto } from "@/lib/api"
import { cn } from "@/lib/utils"

function AssetThumb({ asset, label }: { asset: AssetDto; label: string }) {
  return (
    <a
      href={asset.url}
      target="_blank"
      rel="noreferrer"
      className="group relative block size-20 shrink-0 overflow-hidden rounded-md border bg-muted"
      title={`${label}: ${asset.file_name}`}
    >
      <img
        src={asset.thumbnail_url}
        alt={`${label} ${asset.file_name}`}
        loading="lazy"
        className="size-full object-cover"
      />
      <span
        className={cn(
          "absolute top-1 left-1 rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase",
          asset.owner_role === "primary" ? "bg-emerald-600 text-white" : "bg-orange-600 text-white",
        )}
      >
        {asset.owner_role}
      </span>
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

export function PairRow({ pair, onToggle }: PairRowProps) {
  return (
    <div className="flex items-center gap-4 border-b px-4 py-3 last:border-b-0">
      <div className="flex items-center gap-2">
        <AssetThumb asset={pair.keeper} label="keeper" />
        <div className="text-xs text-muted-foreground">=</div>
        <AssetThumb asset={pair.loser} label="loser" />
      </div>

      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="truncate font-medium">{pair.keeper.file_name}</span>
          {pair.keeper.type === "VIDEO" && <Badge variant="outline">video</Badge>}
          {pair.live_photo !== "aligned" && (
            <Badge variant="destructive">
              {pair.live_photo === "keeper-lacks-motion" ? "keeper lacks motion" : "loser lacks motion"}
            </Badge>
          )}
        </div>
        <div className="mt-0.5 text-xs text-muted-foreground">
          {pair.keeper.taken_at ? new Date(pair.keeper.taken_at).toLocaleString() : "unknown date"} ·{" "}
          {humanBytes(pair.loser.size_bytes)} reclaimable
          {pair.loser.albums && pair.loser.albums.length > 0 && (
            <> · in {pair.loser.albums.map((a) => a.name).join(", ")}</>
          )}
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
