import { useState } from "react"
import { CheckCircle2, Loader2, PlugZap, XCircle } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { api, type ConfigDto } from "@/lib/api"

interface ConnectionFormProps {
  current: ConfigDto | null
  onConfigured: () => void
}

export function ConnectionForm({ current, onConfigured }: ConnectionFormProps) {
  const [url, setUrl] = useState(current?.immich_url ?? "")
  const [primaryEmail, setPrimaryEmail] = useState(current?.primary_email ?? "")
  const [secondaryEmail, setSecondaryEmail] = useState(current?.secondary_email ?? "")
  const [primaryKey, setPrimaryKey] = useState("")
  const [secondaryKey, setSecondaryKey] = useState("")
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<ConfigDto | null>(null)

  async function save() {
    setBusy(true)
    setError(null)
    try {
      const payload = await api.setConfig({
        immich_url: url,
        primary_email: primaryEmail,
        secondary_email: secondaryEmail,
        primary_api_key: primaryKey,
        secondary_api_key: secondaryKey,
      })
      setResult(payload)
      if (payload.configured && payload.partners_bidirectional) {
        onConfigured()
      }
    } catch (cause) {
      setError(String(cause))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card className="mx-auto w-full max-w-xl">
      <CardHeader>
        <CardTitle>Connect to your Immich server</CardTitle>
        <CardDescription>
          Needs the URL you use in the browser, both users&apos; emails, and an API key per user
          (Immich → Account Settings → API Keys). Saved to your local .env.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-3">
        <div className="grid gap-1.5">
          <Label htmlFor="url">Immich URL</Label>
          <Input
            id="url"
            placeholder="https://photos.example.com"
            value={url}
            onChange={(event) => setUrl(event.target.value)}
          />
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor="primary-email">Primary email (keeps the photos)</Label>
          <Input
            id="primary-email"
            type="email"
            placeholder="alice@example.com"
            value={primaryEmail}
            onChange={(event) => setPrimaryEmail(event.target.value)}
          />
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor="primary-key">Primary API key</Label>
          <Input
            id="primary-key"
            type="password"
            placeholder={current?.primary_key_set ? "unchanged" : "from Immich settings"}
            value={primaryKey}
            onChange={(event) => setPrimaryKey(event.target.value)}
          />
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor="secondary-email">Secondary email (duplicates get trashed)</Label>
          <Input
            id="secondary-email"
            type="email"
            placeholder="bob@example.com"
            value={secondaryEmail}
            onChange={(event) => setSecondaryEmail(event.target.value)}
          />
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor="secondary-key">Secondary API key</Label>
          <Input
            id="secondary-key"
            type="password"
            placeholder={current?.secondary_key_set ? "unchanged" : "from Immich settings"}
            value={secondaryKey}
            onChange={(event) => setSecondaryKey(event.target.value)}
          />
        </div>

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

        <Button onClick={save} disabled={busy || !url || !primaryEmail || !secondaryEmail}>
          {busy ? <Loader2 className="animate-spin" /> : <PlugZap />} Save &amp; check connection
        </Button>
      </CardContent>
    </Card>
  )
}
