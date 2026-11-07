import { useId, useState } from 'react'
import { useSearchParams } from 'react-router'
import { AudioLines, Clapperboard, Film, Waves } from 'lucide-react'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { EmptyState, ErrorState } from '@/components/studio/states'
import { SwitchRow } from '@/components/studio/switch-row'
import { CameraChip } from '@/components/camera/CameraRack'
import { BrandDockChip, ModelChip } from '@/components/generate/DockChips'
import { GenerateButton } from '@/components/generate/GenerateButton'
import { GeneratorPage, ResultsHeading } from '@/components/generate/GeneratorPage'
import { PromptDock } from '@/components/generate/PromptDock'
import { RefSlot } from '@/components/generate/RefSlot'
import { SessionResults } from '@/components/generate/SessionResults'
import { useDockModels } from '@/components/generate/useDockModels'
import { useMagicPrompt } from '@/components/generate/useMagicPrompt'
import { useRecordRun } from '@/components/generate/useRecordRun'
import { useBrandChoice } from '@/hooks/useBrandKits'
import { useGenEstimate } from '@/hooks/useGenEstimate'
import { flatItems, useMediaList } from '@/hooks/useMedia'
import { useVideoGenerate } from '@/hooks/useModels'
import { formatDuration } from '@/lib/duration'
import { localEstimate } from '@/lib/estimate'
import { has, normalizeModelId } from '@/lib/models'
import type { ModelInfo, VideoGenerateRequest } from '@/lib/types'
import { cameraSummary } from '@/lib/camera'
import { plainPrompt } from '@/lib/mentions'
import { clampDuration, DEFAULT_I2V_FORM, i2vBlockedReason, i2vPayload, smoothBlockedReason, speechHint, VIDEO_MENTIONS, videoResult, type I2vForm } from '@/lib/video'
import { announce } from '@/stores/ui'
import { LengthChip } from './videoDock'

const PAGE = 'video-i2v'

interface I2vRun {
  form: I2vForm
  body: VideoGenerateRequest
  summary: string
}

export function Img2VidPage() {
  const uid = useId()
  const [params] = useSearchParams()
  const wanted = normalizeModelId(params.get('model'))
  const [form, setForm] = useState<I2vForm>({ ...DEFAULT_I2V_FORM, startId: params.get('image'), model: wanted ?? undefined })
  const set = <K extends keyof I2vForm>(k: K, v: I2vForm[K]) => setForm((f) => ({ ...f, [k]: v }))
  const brand = useBrandChoice()
  const generate = useVideoGenerate()
  const record = useRecordRun<I2vRun>(PAGE)
  const results = useMediaList({ kind: 'video', origin: 'generated' })

  const blocked = (m: ModelInfo) => i2vBlockedReason(m, !!form.endId)
  const { models, model, send, effective } = useDockModels('video', form.model, { blocked, prompt: form.prompt })
  const duration = clampDuration(form.durationS, effective)
  const smoothOff = smoothBlockedReason(effective, duration)
  const magic = useMagicPrompt(form.prompt, { kind: 'video', model: send?.id, brandKitId: brand.sendId })
  const estimate = useGenEstimate(effective ? { kind: 'video', model: effective.id, duration_s: duration } : null, localEstimate(effective, { durationS: duration }))

  const why = !form.startId ? 'Add a start picture first.' : !send || blocked(send) ? 'Pick a model that can animate a picture.' : plainPrompt(form.prompt).trim().length < 3 ? 'Describe the motion first.' : null

  // a run from the dock empties the prompt once it's queued; a tile's Retry leaves the dock alone
  const run = (r: I2vRun, fromDock = false) =>
    generate.mutate(r.body, {
      onSuccess: (res) => {
        const { items, jobs } = videoResult(res)
        record({ prompt: r.body.prompt, summary: r.summary, settings: r, items, jobs, label: 'Your clip' })
        announce('Animating your picture. The clip appears below when it finishes.')
        if (fromDock) setForm((f) => ({ ...f, prompt: '' }))
      },
    })

  const submit = () => {
    if (why || generate.isPending || !send || !form.startId) return
    const body = { ...i2vPayload({ ...form, startId: form.startId, durationS: duration }, send, brand.sendId), ...magic.fields() }
    const summary = [model?.label, formatDuration(duration), form.endId ? 'start → end' : 'from a picture', cameraSummary(form.camera) || null, body.smooth_motion ? 'smooth ×2' : null]
      .filter(Boolean)
      .join(' · ')
    run({ form: { ...form, model: model?.id, durationS: duration }, body, summary }, true)
  }

  const refill = (f: Partial<I2vForm>) => {
    setForm((cur) => ({ ...cur, ...f }))
    announce('Settings copied into the prompt dock.')
  }

  return (
    <GeneratorPage
      label="Image to video"
      targets={[
        { id: 'start', label: 'Start frame', hint: 'The clip opens on this picture', use: (m) => set('startId', m.id) },
        { id: 'end', label: 'End frame', hint: 'The clip lands on this picture', use: (m) => set('endId', m.id) },
      ]}
    >
      <PromptDock
        into="video"
        title="Image to Video"
        icon={<Clapperboard aria-hidden className="size-4 text-studio-accent-hover" />}
        headerExtra={<p className="text-small text-studio-muted max-lg:hidden">Add an end picture to say where it lands.</p>}
        refs={
          <div className="flex items-start gap-3">
            <RefSlot label="Start image" required value={form.startId} onChange={(id) => set('startId', id)} removeLabel="Remove the start image" description="The clip opens on this frame and moves from there." />
            <RefSlot label="End image" value={form.endId} onChange={(id) => set('endId', id)} removeLabel="Remove the end image" description="The clip lands on this frame." />
          </div>
        }
        promptLabel="Describe the motion"
        prompt={form.prompt}
        onPrompt={(v) => set('prompt', v)}
        mentions={VIDEO_MENTIONS}
        placeholder="e.g. Slow push in, steam rises from the cup, leaves drift past the window"
        chips={
          <>
            <ModelChip
              models={models}
              model={model}
              onChange={(id) => setForm((f) => ({ ...f, model: id, durationS: clampDuration(f.durationS, models.find((m) => m.id === id)) }))}
              blockedBy={blocked}
              highlighted={!!wanted}
              extra={
                <p className="flex items-start gap-2 rounded-[6px] border border-studio-border bg-studio-raised p-2.5 text-small text-studio-muted">
                  <AudioLines aria-hidden className="mt-0.5 size-3.5 shrink-0 text-studio-accent-hover" />
                  {has(effective, 'audio')
                    ? `${effective?.label} adds sound that fits the motion: ambience and effects. Describe sounds in the prompt to steer it.`
                    : 'This model makes silent clips.'}
                </p>
              }
            />
            <LengthChip model={effective} value={duration} onChange={(v) => set('durationS', v)} estimate={estimate} />
            <CameraChip value={form.camera ?? {}} onChange={(c) => set('camera', c)} />
            <BrandDockChip choice={brand} />
          </>
        }
        advanced={
          <div className="flex flex-wrap items-start gap-4">
            <div className="w-40">
              <Label htmlFor={`${uid}-seed`} className="mb-1.5">
                Seed
              </Label>
              <Input id={`${uid}-seed`} inputMode="numeric" value={form.seed} onChange={(e) => set('seed', e.target.value.replace(/\D/g, ''))} placeholder="Random" className="font-mono" />
            </div>
            {effective?.smooth_motion && (
              <SwitchRow
                id={`${uid}-smooth`}
                checked={form.smooth && !smoothOff}
                disabled={!!smoothOff}
                onChange={(v) => set('smooth', v)}
                icon={<Waves aria-hidden className="size-3.5" />}
                title="Smooth motion ×2"
                hint={smoothOff ?? 'Doubles the frame rate (24 → 48 fps). Adds render time.'}
              />
            )}
          </div>
        }
        magic={magic}
        error={generate.isError ? <ErrorState compact title="Couldn't start the clip" error={generate.error} /> : null}
        footer={
          <GenerateButton
            verb="Animate"
            what={`${formatDuration(duration)} clip`}
            estimate={estimate}
            icon={<Film aria-hidden />}
            blocked={why}
            note={speechHint(form.prompt) ?? (form.endId ? 'Moves from your start picture to your end picture.' : 'Opens on your picture and moves from there.')}
            pending={generate.isPending}
          />
        }
        onSubmit={submit}
      />
      <ResultsHeading>Your clips</ResultsHeading>
      <SessionResults<I2vRun>
        page={PAGE}
        label="Results"
        items={flatItems(results.data)}
        loading={results.isPending}
        error={results.isError ? results.error : undefined}
        onRetryLoad={() => results.refetch()}
        hasMore={results.hasNextPage}
        loadingMore={results.isFetchingNextPage}
        onLoadMore={() => results.fetchNextPage()}
        onRetry={(req) => run(req.settings)}
        onReuse={(row) => refill(row.request?.settings.form ?? { prompt: row.prompt })}
        onUseAsRef={(m) => set('startId', m.id)}
        refLabel="Use as start frame"
        reuseItem={(m) => refill({ prompt: m.prompt ?? '' })}
        empty={
          <EmptyState icon={<Film />} title="No clips yet">
            Add a picture and describe the motion. Each clip appears here as it finishes.
          </EmptyState>
        }
      />
    </GeneratorPage>
  )
}
