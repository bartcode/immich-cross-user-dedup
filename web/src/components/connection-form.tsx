import { useState } from "react"
import { CheckCircle2, Info, Loader2, PlugZap, Plus, Trash2, XCircle } from "lucide-react"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { api, type ConfigDto } from "@/lib/api"

const READ_SCOPES = ["user.read", "partner.read", "asset.read", "asset.view", "album.read", "asset.statistics"]
const ALBUM_WRITE_SCOPES = ["albumAsset.create", "albumAsset.delete"]
const ALBUM_SHARE_SCOPES = ["albumUser.create", "albumUser.delete"]

function ScopeList({ scopes }: { scopes: string[] }) {
  return (
    <span className="font-mono text-[11px] leading-4">
      {scopes.map((scope, index) => (
        <span key={scope}>
          {index > 0 && <span className="text-muted-foreground"> · </span>}
          {scope}
        </span>
      ))}
    </span>
  )
}

interface SecondaryRow {
  email: string
  apiKey: string
}

interface ConnectionFormProps {
  current: ConfigDto | null
  onConfigured: () => void
}

export function ConnectionForm({ current, onConfigured }: ConnectionFormProps) {
  const [url, setUrl] = useState(current?.immich_url ?? "")
  const [primaryEmail, setPrimaryEmail] = useState(current?.primary_email ?? "")
  const [primaryKey, setPrimaryKey] = useState("")
  const [secondaries, setSecondaries] = useState<SecondaryRow[]>(
    current?.secondaries.map((secondary) => ({ email: secondary.email, apiKey: "" })) ?? [
      { email: "", apiKey: "" },
    ],
  )
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<ConfigDto | null>(null)

  function updateSecondary(index: number, patch: Partial<SecondaryRow>) {
    setSecondaries((rows) => rows.map((row, i) => (i === index ? { ...row, ...patch } : row)))
  }

  async function save() {
    setBusy(true)
    setError(null)
    try {
      const payload = await api.setConfig({
        immich_url: url,
        primary_email: primaryEmail,
        primary_api_key: primaryKey,
        secondaries: secondaries
          .filter((row) => row.email.trim())
          .map((row) => ({ email: row.email, api_key: row.apiKey })),
      })
      setResult(payload)
      if (payload.configured && payload.partners_ok) {
        onConfigured()
      }
    } catch (cause) {
      setError(String(cause))
    } finally {
      setBusy(false)
    }
  }

  const canSave =
    url.trim() !== "" &&
    primaryEmail.trim() !== "" &&
    secondaries.some((row) => row.email.trim() !== "")

  return (
    <Card className="mx-auto w-full max-w-xl">
      <CardHeader>
        <CardTitle>Connect to your Immich server</CardTitle>
        <CardDescription>
          One primary user (keeps the copies) plus every secondary user whose duplicates should be
          removed. Needs an API key per user (Immich → Account Settings → API Keys). Saved to your
          local .env.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        <div className="grid gap-1.5">
          <Label htmlFor="url">Immich URL</Label>
          <Input
            id="url"
            placeholder="https://photos.example.com"
            value={url}
            onChange={(event) => setUrl(event.target.value)}
          />
        </div>

        <div className="grid gap-1.5 rounded-md border p-3">
          <div className="flex items-center justify-between">
            <Label htmlFor="primary-email">Primary — keeps the photos</Label>
          </div>
          <div className="grid gap-1.5 sm:grid-cols-2">
            <Input
              id="primary-email"
              type="email"
              placeholder="alice@example.com"
              value={primaryEmail}
              onChange={(event) => setPrimaryEmail(event.target.value)}
            />
            <Input
              type="password"
              placeholder={current?.primary_key_set ? "API key (unchanged)" : "API key"}
              value={primaryKey}
              onChange={(event) => setPrimaryKey(event.target.value)}
            />
          </div>
          <p className="text-xs text-muted-foreground">
            Select these scopes for the key: <ScopeList scopes={[...READ_SCOPES, ...ALBUM_WRITE_SCOPES]} />{" "}
            — plus <span className="font-mono text-[11px]">asset.update</span> if you will use
            merge-metadata. Lists this user&apos;s library, joins their copy to albums they own, and
            optionally merges favorites/descriptions onto their copies.{" "}
            <strong>Nothing is ever deleted with this key.</strong>
          </p>
        </div>

        {secondaries.map((row, index) => (
          <div key={index} className="grid gap-1.5 rounded-md border p-3">
            <div className="flex items-center justify-between">
              <Label>Secondary — duplicates get trashed</Label>
              <Button
                variant="ghost"
                size="icon"
                className="size-6"
                aria-label="Remove secondary user"
                onClick={() => setSecondaries((rows) => rows.filter((_, i) => i !== index))}
              >
                <Trash2 className="size-3.5" />
              </Button>
            </div>
            <div className="grid gap-1.5 sm:grid-cols-2">
              <Input
                type="email"
                placeholder="bob@example.com"
                value={row.email}
                onChange={(event) => updateSecondary(index, { email: event.target.value })}
              />
              <Input
                type="password"
                placeholder={
                  current?.secondaries.find((s) => s.email === row.email)?.key_set
                    ? "API key (unchanged)"
                    : "API key"
                }
                value={row.apiKey}
                onChange={(event) => updateSecondary(index, { apiKey: event.target.value })}
              />
            </div>
            <p className="text-xs text-muted-foreground">
              Select these scopes for the key:{" "}
              <ScopeList scopes={[...READ_SCOPES, ...ALBUM_WRITE_SCOPES, ...ALBUM_SHARE_SCOPES, "asset.delete"]} />{" "}
              — lists this user&apos;s library, adds the keeper to albums this user owns (sharing
              affected albums with the primary as editor when partner sharing is off), and moves{" "}
              <strong>this user&apos;s own</strong> duplicates to trash (restorable via undo). Never
              touches anyone else&apos;s assets.
            </p>
          </div>
        ))}
        <Button
          variant="outline"
          size="sm"
          className="justify-self-start"
          onClick={() => setSecondaries((rows) => [...rows, { email: "", apiKey: "" }])}
        >
          <Plus /> Add secondary user
        </Button>

        <Alert>
          <Info className="size-4" />
          <AlertTitle>API key scopes &amp; sharing</AlertTitle>
          <AlertDescription>
            When creating each key in Immich (Account Settings → API Keys), select the scopes listed
            under the corresponding input — or simply the <span className="font-mono text-xs">all</span>{" "}
            scope. Every required scope (read AND write) is verified against your server when you
            save — missing scopes are named per key. Partner sharing is{" "}
            <strong>optional</strong>: without it, affected albums are shared with the primary as
            editor during apply and revoked on undo — nobody gets access to anyone&apos;s full library.
          </AlertDescription>
        </Alert>

        {error && <p className="text-sm text-destructive">{error}</p>}

        {result && (
          <ul className="grid gap-1 rounded-md border p-2 text-sm">
            {result.checks.map((check) => (
              <li key={check.name} className="flex items-start gap-2">
                {check.ok ? (
                  <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-emerald-600" />
                ) : (
                  <XCircle className="mt-0.5 size-4 shrink-0 text-destructive" />
                )}
                <span>
                  <span className="font-medium">{check.name}:</span> {check.detail}
                </span>
              </li>
            ))}
          </ul>
        )}

        <Button onClick={save} disabled={busy || !canSave}>
          {busy ? <Loader2 className="animate-spin" /> : <PlugZap />} Save &amp; check connection
        </Button>
      </CardContent>
    </Card>
  )
}
