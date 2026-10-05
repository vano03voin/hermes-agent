import { useEffect, useState } from "react";
import { Button } from "@nous-research/ui/ui/components/button";
import { memoryApi, type MemoryDocument } from "@/lib/memory-api";
import { errorMessage } from "@/lib/api-error";
import { useProfileScope } from "@/contexts/useProfileScope";

export default function MemoryPage() {
  const scope = useProfileScope();
  const [target, setTarget] = useState<"memory" | "user">("memory");
  const [document, setDocument] = useState<MemoryDocument | null>(null);
  const [content, setContent] = useState("");
  const [status, setStatus] = useState("");
  const [saving, setSaving] = useState(false);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let current = true;
    setDocument(null);
    setStatus("");
    memoryApi.getMemoryDocument(target).then((value) => {
      if (current) { setDocument(value); setContent(value.content); }
    }).catch((error: unknown) => { if (current) setStatus(errorMessage(error)); });
    return () => { current = false; };
  }, [target, scope.profile, revision]);

  async function save() {
    if (!document) return;
    setSaving(true);
    try {
      const value = await memoryApi.saveMemoryDocument(target, content, document.version);
      setDocument(value);
      setStatus("Saved. New conversations use the updated memory.");
    } catch (error) { setStatus(errorMessage(error)); }
    finally { setSaving(false); }
  }

  return <section className="flex max-w-4xl flex-col gap-4 p-6">
    <h1 className="text-2xl font-semibold">Memory</h1>
    <p className="text-sm text-muted-foreground">Review what your assistant remembers. Changes apply to new conversations.</p>
    <div className="flex gap-2">
      <Button outlined={target !== "memory"} aria-pressed={target === "memory"} disabled={saving} onClick={() => setTarget("memory")}>Assistant notes</Button>
      <Button outlined={target !== "user"} aria-pressed={target === "user"} disabled={saving} onClick={() => setTarget("user")}>About you</Button>
    </div>
    <textarea aria-label={target === "memory" ? "Assistant notes" : "About you"}
      className="min-h-80 rounded border border-border bg-background p-3 font-mono text-sm"
      value={content} onChange={(event) => setContent(event.target.value)} disabled={!document || saving} />
    <div className="flex gap-2">
      <Button onClick={() => void save()} disabled={!document || saving}>{saving ? "Saving…" : "Save"}</Button>
      <Button outlined onClick={() => setRevision((value) => value + 1)} disabled={saving}>Reload</Button>
    </div>
    <p role="status" className="text-sm">{status}</p>
  </section>;
}
