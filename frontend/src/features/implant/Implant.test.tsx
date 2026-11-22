import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Rail } from '@/components/shell/Rail'
import { qk } from '@/hooks/keys'
import { cropBox, type ImplantPicture, type OptionCheck, type Patient } from '@/lib/implant'
import { activeRail, pageTitle } from '@/lib/nav'
import type { User } from '@/lib/types'
import { mockApi, renderAt, systemOk } from '@/test/media-fixtures'
import { ImplantLayout } from './ImplantLayout'
import { ImplantPatientPage } from './ImplantPatientPage'
import { ImplantPatientsPage } from './ImplantPatientsPage'
import { MyPreviewPage } from './MyPreviewPage'
import { torsoShapes } from './torsoShape'

afterEach(() => vi.unstubAllGlobals())

// jsdom has no WebGL; the body model's three.js half is checked by its shapes (torsoShape) instead
const torso = { set: vi.fn(), rotate: vi.fn(), reset: vi.fn(), dispose: vi.fn() }
vi.mock('./torso3d', () => ({ createTorso: vi.fn(() => torso) }))

const member: User = { id: 'u2', email: 'dr@clinic.test', display_name: 'Dr Rana', workspace_id: 'w1', role: 'owner', admin: false, verified: true }
const surgeon: User = { ...member, implant: true }

const photo = (view: ImplantPicture['view'], extra: Partial<ImplantPicture> = {}): ImplantPicture => ({
  id: `ph-${view}`,
  status: 'ready',
  url: `/api/implant/files/ph-${view}`,
  job_id: null,
  view,
  garment: 'garment',
  ...extra,
})

function patient(extra: Partial<Patient> = {}): Patient {
  return {
    id: 'p1',
    label: 'P-0001',
    adult_confirmed: true,
    date_of_birth: '1994-05-02',
    age: 32,
    minor: false,
    patient_linked: false,
    patient_account: { linked: false, name: null, invite_expires_at: null },
    consent: { at: '2026-10-24T09:00:00Z', attested_by: 'Dr Rana', topless_ok: false, form: false },
    retention_months: 24,
    last_activity_at: '2026-10-24T09:00:00Z',
    created_at: '2026-10-24T09:00:00Z',
    approved: false,
    measurements: {},
    notes: '',
    views: { front: 'Front', left_oblique: 'Left 45°', right_oblique: 'Right 45°', left_lateral: 'Left 90°', right_lateral: 'Right 90°' },
    photos: { front: photo('front'), left_oblique: photo('left_oblique'), right_oblique: null },
    sessions: [],
    chart: { session_id: null, option: null, approved_at: null, versions: 0, url: null },
    ...extra,
  }
}

const OPTIONS = [300, 350].map((cc) => ({ label: '', left_cc: cc, right_cc: cc, profile: 'moderate_plus' as const, shape: 'round' as const, placement: 'dual_plane' as const, fill: 'silicone' as const, procedure: 'augmentation' as const }))

const withResults = (status: ImplantPicture['status'] = 'ready') =>
  patient({
    sessions: [
      {
        id: 's1',
        created_at: '2026-10-24T09:30:00Z',
        options: OPTIONS,
        results: [0, 1].map((option) => ({ id: `r${option}`, status, url: status === 'ready' ? `/api/implant/files/r${option}` : null, job_id: `j${option}`, option, view: 'front' as const })),
      },
    ],
  })

function openPatient(p: Patient = patient(), extra: (method: string, path: string, body: unknown) => unknown = () => undefined) {
  const calls = mockApi((method, path, _q, body) => {
    const hit = extra(method, path, body)
    if (hit !== undefined) return hit
    if (path === '/auth/me') return surgeon
    if (path === '/implant/patients/p1') return p
  })
  renderAt('/clinic/implant/p1', [{ path: '/clinic/implant/:id', element: <ImplantPatientPage /> }], (qc) => qc.setQueryData(['me'], surgeon))
  return { calls, user: userEvent.setup() }
}

describe('Implant Preview access', () => {
  it('shows the rail entry only to admins and granted surgeons', async () => {
    mockApi((_m, p) => {
      if (p === '/system/status') return systemOk
      if (p === '/auth/me') return member
    })
    renderAt('/', [], (qc) => qc.setQueryData(qk.jobs, []), <Rail />)
    const rail = screen.getByRole('navigation', { name: 'Main' })
    await within(rail).findByRole('link', { name: /^Settings\./ })
    expect(within(rail).queryByRole('link', { name: 'Implant Preview' })).toBeNull()
    vi.unstubAllGlobals()
    mockApi((_m, p) => {
      if (p === '/system/status') return systemOk
      if (p === '/auth/me') return surgeon
    })
    renderAt('/clinic/implant/p1', [], (qc) => qc.setQueryData(qk.jobs, []), <Rail />)
    const link = await screen.findByRole('link', { name: 'Implant Preview' })
    expect(link).toHaveAttribute('href', '/clinic/implant')
    expect(link).toHaveAttribute('aria-current', 'page')
    // a surgeon who isn't an admin gets no Print Studio
    expect(screen.queryByRole('link', { name: 'Print Studio' })).toBeNull()
    expect(activeRail('/clinic/implant')).toBe('implant')
    expect(pageTitle('/clinic/implant/p1')).toBe('Implant Preview')
  })

  it('sends anyone else home from the route', async () => {
    mockApi((_m, p) => (p === '/auth/me' ? member : undefined))
    renderAt('/clinic/implant', [{ path: '/clinic/implant', element: <ImplantLayout /> }, { path: '/', element: <p>home</p> }], (qc) => qc.setQueryData(['me'], member))
    expect(await screen.findByText('home')).toBeInTheDocument()
  })
})

describe('Implant patients', () => {
  it('keeps Create disabled until the date of birth and consent are in', async () => {
    const calls = mockApi((method, path) => {
      if (path === '/auth/me') return surgeon
      if (path === '/implant/patients' && method === 'GET') return []
      if (path === '/implant/patients' && method === 'POST') return patient()
    })
    renderAt('/clinic/implant', [{ path: '/clinic/implant', element: <ImplantPatientsPage /> }, { path: '/clinic/implant/:id', element: <p>patient page</p> }], (qc) => qc.setQueryData(['me'], surgeon))
    const user = userEvent.setup()
    expect(await screen.findByText('Add one to start a consultation.')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'New patient' }))
    const dialog = screen.getByRole('dialog', { name: 'New patient' })
    const create = within(dialog).getByRole('button', { name: 'Create' })
    await user.type(within(dialog).getByLabelText('Patient code or name'), 'P-0001')
    expect(create).toBeDisabled()
    expect(create).toHaveAccessibleDescription(/the date of birth, the consent tick/)
    fireEvent.change(within(dialog).getByLabelText('Date of birth'), { target: { value: '1994-05-02' } })
    expect(within(dialog).getByText(/years old/)).toBeInTheDocument()
    expect(create).toBeDisabled()
    await user.click(within(dialog).getByRole('checkbox', { name: /patient signed the consent form/ }))
    expect(create).toBeEnabled()
    await user.click(create)
    await waitFor(() => expect(screen.getByText('patient page')).toBeInTheDocument())
    expect(calls.find((c) => c.method === 'POST')?.body).toEqual({ label: 'P-0001', date_of_birth: '1994-05-02', consent_attested: true, topless_ok: false, retention_months: 24 })
  })

  it('asks for a guardian under 18 and drops the topless choice', async () => {
    const calls = mockApi((method, path) => {
      if (path === '/auth/me') return surgeon
      if (path === '/implant/patients' && method === 'GET') return []
      if (path === '/implant/patients' && method === 'POST') return patient({ minor: true })
    })
    renderAt('/clinic/implant', [{ path: '/clinic/implant', element: <ImplantPatientsPage /> }, { path: '/clinic/implant/:id', element: <p>patient page</p> }], (qc) => qc.setQueryData(['me'], surgeon))
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'New patient' }))
    const dialog = screen.getByRole('dialog', { name: 'New patient' })
    await user.type(within(dialog).getByLabelText('Patient code or name'), 'Teen')
    const year = new Date().getFullYear() - 16
    fireEvent.change(within(dialog).getByLabelText('Date of birth'), { target: { value: `${year}-01-01` } })
    expect(within(dialog).queryByRole('checkbox', { name: /Topless/ })).toBeNull()
    await user.click(within(dialog).getByRole('checkbox', { name: /patient signed the consent form/ }))
    const create = within(dialog).getByRole('button', { name: 'Create' })
    expect(create).toHaveAccessibleDescription(/guardian’s name and consent/)
    await user.type(within(dialog).getByLabelText('Guardian’s name'), 'Mrs Khan')
    await user.click(within(dialog).getByRole('checkbox', { name: /guardian signed/ }))
    await user.click(create)
    await waitFor(() => expect(screen.getByText('patient page')).toBeInTheDocument())
    expect(calls.find((c) => c.method === 'POST')?.body).toMatchObject({ date_of_birth: `${year}-01-01`, topless_ok: false, guardian: { name: 'Mrs Khan', relation: '', consented: true } })
  })
})

describe('Implant patient page', () => {
  it('holds back Topless without consent and explains why', async () => {
    openPatient()
    const slot = (await screen.findByRole('heading', { name: 'Front' })).closest('li')!
    const topless = within(slot).getByRole('radio', { name: 'Topless' })
    expect(topless).toHaveAttribute('aria-disabled', 'true')
    expect(topless).toHaveAccessibleDescription(/separate consent/)
    expect(within(slot).getByRole('radio', { name: 'Bra/top' })).toBeChecked()
    expect(cropBox(15, 100)).toBe('0,0.15,1,0.85')
  })

  it('her own photo comes first, is always in her clothes, and the clinical views keep their choice', async () => {
    const p = patient()
    p.consent.topless_ok = true
    p.views = { own: 'Her own photo', ...p.views }
    openPatient(p)
    const photos = await screen.findByRole('region', { name: 'Photos' })
    expect(within(photos).getAllByRole('heading', { level: 3 })[0]).toHaveTextContent('Her own photo')
    const own = within(photos).getByRole('heading', { name: 'Her own photo' }).closest('li')!
    expect(within(own).queryByRole('radio')).not.toBeInTheDocument()
    expect(within(own).getByText(/dupatta off the chest/)).toBeInTheDocument()
    const front = within(photos).getByRole('heading', { name: 'Front' }).closest('li')!
    expect(within(front).getByRole('radio', { name: 'Topless' })).toHaveAttribute('aria-disabled', 'false')
  })

  it('renders the options with the spec and the estimate', async () => {
    const { calls, user } = openPatient(patient(), (method, path) => (method === 'POST' && path === '/implant/patients/p1/sessions' ? withResults('queued') : undefined))
    await user.click(await screen.findByRole('button', { name: '300 / 350 / 400 cc moderate-plus' }))
    const b = screen.getByRole('group', { name: 'Option B' })
    await user.selectOptions(within(b).getByLabelText('Procedure'), 'Augmentation with lift (mastopexy)')
    const go = screen.getByRole('button', { name: /Render the options/ })
    // the AI edit and the shape warp, both ticked to begin with
    expect(go).toHaveTextContent('3 options × 2 photos × 2 ways = 12 pictures')
    await user.click(screen.getByRole('checkbox', { name: /^Shape warp/ }))
    expect(go).toHaveTextContent('3 options × 2 photos = 6 pictures')
    await user.click(go)
    await waitFor(() => expect(calls.find((c) => c.path === '/implant/patients/p1/sessions')).toBeDefined())
    const body = calls.find((c) => c.path === '/implant/patients/p1/sessions')!.body as { options: unknown[]; engines: string[] }
    expect(body.engines).toEqual(['ai'])
    expect(body.options).toEqual(
      [300, 350, 400].map((cc) => ({
        label: '',
        left_cc: cc,
        right_cc: cc,
        profile: 'moderate_plus',
        shape: 'round',
        placement: 'dual_plane',
        fill: 'silicone',
        procedure: cc === 350 ? 'augmentation_lift' : 'augmentation',
      })),
    )
    expect(await screen.findAllByText('Waiting in queue')).toHaveLength(2)
  })

  it('approves an option with the surgeon’s notes', async () => {
    const { calls, user } = openPatient(withResults(), (method, path) =>
      method === 'POST' && path === '/implant/patients/p1/sessions/s1/approve'
        ? { ...withResults(), approved: true, chart: { session_id: 's1', option: 1, approved_at: '2026-10-24T10:00:00Z', versions: 1, url: '/api/implant/patients/p1/chart.pdf' } }
        : undefined,
    )
    // scoped: role queries over the whole page are slow in jsdom
    const approve = await screen.findByRole('list', { name: 'Approve an option' })
    await user.click(within(approve).getByRole('button', { name: /^Approve this option….*option B/ }))
    await user.type(within(approve).getByLabelText('Surgeon’s notes for option B'), 'Dual plane, IMF incision')
    await user.click(within(approve).getByRole('button', { name: 'Approve and make the chart' }))
    await waitFor(() => expect(calls.find((c) => c.path === '/implant/patients/p1/sessions/s1/approve')?.body).toEqual({ option: 1, engine: 'ai', notes: 'Dual plane, IMF incision' }))
    expect(await screen.findByText('Chart approved 24 Oct · option B · version 1')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Download chart' })).toHaveAttribute('href', '/api/implant/patients/p1/chart.pdf')
  })

  it('opens a finished picture against its photo', async () => {
    const { user } = openPatient(withResults())
    await user.click(await screen.findByRole('button', { name: 'Option A, Front: compare with the photo' }))
    const dialog = screen.getByRole('dialog', { name: 'Option A, Front' })
    expect(within(dialog).getByRole('slider')).toBeInTheDocument()
    const srcs = within(dialog)
      .getAllByRole('img')
      .map((i) => i.getAttribute('src'))
    expect(srcs).toEqual(expect.arrayContaining(['/api/implant/files/ph-front', '/api/implant/files/r0']))
  })

  it('gives every view from the server its own slot and results row', async () => {
    const p = withResults()
    p.photos.left_lateral = photo('left_lateral')
    p.sessions[0].results.push({ id: 'r9', status: 'ready', url: '/api/implant/files/r9', job_id: null, option: 0, view: 'left_lateral' })
    openPatient(p)
    const photos = await screen.findByRole('region', { name: 'Photos' })
    expect(within(photos).getAllByRole('heading', { level: 3 }).map((h) => h.textContent)).toEqual(['Front', 'Left 45°', 'Right 45°', 'Left 90°', 'Right 90°'])
    expect(screen.getByRole('button', { name: 'Option A, Left 90°: compare with the photo' })).toBeInTheDocument()
  })

  it('outlines the chest area and saves the surgeon’s own', async () => {
    const p = patient({ photos: { front: photo('front', { chest_box: [100, 200, 600, 800] }), left_oblique: photo('left_oblique'), right_oblique: null } })
    const { calls, user } = openPatient(p, (method, path, body) => (method === 'PATCH' && path === '/implant/photos/ph-front' ? photo('front', body as Partial<ImplantPicture>) : undefined))
    const front = (await screen.findByRole('heading', { name: 'Front' })).closest('li')!
    const left = screen.getByRole('heading', { name: 'Left 45°' }).closest('li')!
    expect(within(front).getByTestId('chest-box')).toHaveStyle({ top: '10%', left: '20%', height: '50%', width: '60%' })
    expect(within(left).queryByTestId('chest-box')).toBeNull()
    expect(within(left).getByText(/found automatically on the first render/)).toBeInTheDocument()

    await user.click(within(front).getByRole('button', { name: 'Adjust chest area, Front' }))
    expect(within(front).getByText('Everything outside this area stays exactly as in the photo.')).toBeInTheDocument()
    fireEvent.change(within(front).getByLabelText(/^Top: /), { target: { value: '20' } })
    fireEvent.change(within(front).getByLabelText(/^Right: /), { target: { value: '90' } })
    expect(within(front).getByTestId('chest-box')).toHaveStyle({ top: '20%', width: '70%' })
    await user.click(within(front).getByRole('button', { name: 'Save chest area, Front' }))
    await waitFor(() => expect(calls.find((c) => c.method === 'PATCH')).toMatchObject({ path: '/implant/photos/ph-front', body: { chest_box: [200, 200, 600, 900] } }))
  })

  it('shows the implant size and the planner’s warnings under each option', async () => {
    const check = {
      sides: { left: { width_cm: 11.8, projection_cm: 3.9, cup_change: 1.5 }, right: { width_cm: 11.8, projection_cm: 3.9, cup_change: 1.5 } },
      checks: [
        { level: 'warn', text: 'Ptosis grade 2: a lift is likely needed.' },
        { level: 'info', text: 'Her base widths differ by 1.5 cm.' },
      ],
    }
    const { calls } = openPatient(patient({ measurements: { ptosis_grade: 2 } }), (method, path) => (method === 'POST' && path === '/implant/patients/p1/check' ? [check] : undefined))
    expect(await screen.findByText('≈ 11.8 cm wide · 3.9 cm projection · about +1.5 cups')).toBeInTheDocument()
    expect(screen.getByText('Ptosis grade 2: a lift is likely needed.').closest('li')).toHaveClass('text-studio-warning')
    expect(screen.getByText('Her base widths differ by 1.5 cm.')).toHaveClass('text-studio-muted')
    expect(screen.getByText('Approximate; confirm with the manufacturer’s chart.')).toBeInTheDocument()
    expect(screen.getByText('Add her bra size in Measurements to see the expected cup.')).toHaveClass('text-studio-muted')
    const sent = calls.filter((c) => c.path === '/implant/patients/p1/check')
    expect(sent).toHaveLength(1)
    expect(sent[0].body).toEqual({ options: [{ label: '', left_cc: 300, right_cc: 300, profile: 'moderate_plus', shape: 'round', placement: 'dual_plane', fill: 'silicone', procedure: 'augmentation' }] })
  })

  it('names the cup she goes to, per side when they differ', async () => {
    const side = { width_cm: 11.8, projection_cm: 3.9, cup_change: 1.5 }
    const check = { sides: { left: { ...side, target_cup: 'a full B cup' }, right: { ...side, target_cup: 'a C cup' } }, checks: [] }
    openPatient(patient({ measurements: { bra_size: '32A' } }), (method, path) => (method === 'POST' && path === '/implant/patients/p1/check' ? [check] : undefined))
    expect(await screen.findByText('L From her current 32A to a full B cup')).toBeInTheDocument()
    expect(screen.getByText('R From her current 32A to a C cup')).toBeInTheDocument()
    expect(screen.queryByText(/Add her bra size/)).toBeNull()
  })

  it('shows the AI edit and the shape warp in their own rows, and approves either', async () => {
    const p = withResults()
    p.sessions[0].results.push(...[0, 1].map((option) => ({ id: `w${option}`, status: 'ready' as const, url: `/api/implant/files/w${option}`, job_id: null, option, view: 'front' as const, engine: 'warp' as const })))
    const { calls, user } = openPatient(p, (method, path) => (method === 'POST' && path.endsWith('/approve') ? p : undefined))
    expect(await screen.findByRole('button', { name: 'Option A, Front · Shape warp: compare with the photo' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Option A, Front · AI: compare with the photo' })).toBeInTheDocument()
    const approve = screen.getByRole('list', { name: 'Approve an option' })
    await user.click(within(approve).getByRole('button', { name: /option A \(Shape warp\)/ }))
    await user.click(within(approve).getByRole('button', { name: 'Approve and make the chart' }))
    await waitFor(() => expect(calls.find((c) => c.path.endsWith('/approve'))?.body).toEqual({ option: 0, engine: 'warp', notes: '' }))
  })

  it('ends a session-only consultation after asking, and deletes everything', async () => {
    const { calls, user } = openPatient(patient({ retention_months: 0 }), (method, path) => (method === 'POST' && path === '/implant/patients/p1/end' ? null : undefined))
    expect(await screen.findByText(/Session only: the photos and results are deleted/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'End consultation and delete' }))
    expect(calls.some((c) => c.path === '/implant/patients/p1/end')).toBe(false)
    await user.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'End and delete' }))
    await waitFor(() => expect(calls.find((c) => c.method === 'POST' && c.path === '/implant/patients/p1/end')).toBeDefined())
  })

  it('draws the body model from her measurements and the implant size', async () => {
    openPatient(withResults())
    expect(await screen.findByRole('heading', { name: '3D body model' })).toBeInTheDocument()
    await waitFor(() => expect(torso.set).toHaveBeenCalled())
    const check: OptionCheck = { sides: { left: { width_cm: 11.9, projection_cm: 4.5, cup_change: 1.5 }, right: { width_cm: 11.9, projection_cm: 4.5, cup_change: 1.5 } }, checks: [] }
    const { before, after } = torsoShapes({ bra_size: '34B', bw_left_cm: 12, chest_width_cm: 30 }, OPTIONS[0], check)
    expect(before.chestWidth).toBe(30)
    expect(after!.left.projection).toBeGreaterThan(before.left.projection)
    expect(after!.left.radius).toBeGreaterThanOrEqual(before.left.radius)
    expect(torsoShapes({}, undefined, undefined).after).toBeNull()
  })
})

describe('Age, guardian and the patient account', () => {
  it('under 18: no photo slots, no render, no invite, the 3D model stays', async () => {
    openPatient(patient({ minor: true, age: 16, adult_confirmed: false, photos: {}, consent: { at: '2026-10-24T09:00:00Z', attested_by: 'Dr Rana', topless_ok: false, form: false, guardian: { name: 'Mrs Khan', relation: 'mother', consented_at: '2026-10-24T09:00:00Z' } } }))
    const photos = await screen.findByRole('region', { name: 'Photos' })
    expect(within(photos).getByRole('note')).toHaveTextContent(/under 18/)
    expect(within(photos).queryByRole('listitem')).toBeNull()
    expect(screen.getByText(/Guardian: Mrs Khan \(mother\)/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Render the options/ })).toBeDisabled()
    expect(screen.queryByRole('button', { name: /Invite patient/ })).toBeNull()
    expect(screen.getByRole('heading', { name: '3D body model' })).toBeInTheDocument()
  })

  it('asks for the date of birth again on a flagged photo', async () => {
    const { calls, user } = openPatient(patient({ photos: { front: photo('front', { garment: 'topless', age_flag: true }) } }), (method, path) =>
      method === 'POST' && path.endsWith('/confirm-age') ? photo('front') : undefined,
    )
    const slot = (await screen.findByRole('heading', { name: 'Front' })).closest('li')!
    expect(within(slot).getByText(/couldn’t confirm this photo shows an adult/)).toBeInTheDocument()
    expect(within(slot).queryByRole('button', { name: /Adjust chest area/ })).toBeNull()
    expect(screen.getByRole('button', { name: /Render the options/ })).toHaveAccessibleDescription(/flagged photo/)
    fireEvent.change(within(slot).getByLabelText('Date of birth'), { target: { value: '1994-05-02' } })
    await user.click(within(slot).getByRole('button', { name: 'Confirm' }))
    await waitFor(() => expect(calls.find((c) => c.path === '/implant/patients/p1/photos/ph-front/confirm-age')?.body).toEqual({ date_of_birth: '1994-05-02' }))
  })

  it('an older record is asked for its date of birth before an invite', async () => {
    openPatient(patient({ date_of_birth: null, age: null }))
    expect(await screen.findByText(/made before the date of birth was asked/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Invite patient/ })).toBeDisabled()
  })

  it('shows the invite code once, with the keep period sent along', async () => {
    const invited = patient({ retention_months: 6, patient_account: { linked: false, name: null, invite_expires_at: '2026-10-31T09:00:00Z' } })
    const { calls, user } = openPatient(patient(), (method, path) => (method === 'POST' && path === '/implant/patients/p1/invite' ? { code: 'K7M2QXPA', expires_at: '2026-10-31T09:00:00Z', patient: invited } : undefined))
    await user.selectOptions(await screen.findByLabelText('Keep this record for'), '6')
    await user.click(screen.getByRole('button', { name: /Invite patient/ }))
    expect(await screen.findByDisplayValue('K7M2QXPA')).toBeInTheDocument()
    expect(calls.find((c) => c.path === '/implant/patients/p1/invite')?.body).toEqual({ retention_months: 6 })
    expect(screen.getByText(/works once, for 7 days/)).toBeInTheDocument()
  })

  it('unlinks a linked patient after a confirm', async () => {
    const { calls, user } = openPatient(patient({ patient_linked: true, retention_months: 3, patient_account: { linked: true, name: 'Sana', invite_expires_at: null } }), (method, path) =>
      method === 'DELETE' && path === '/implant/patients/p1/patient' ? patient() : undefined,
    )
    expect(await screen.findByText('Linked: Sana')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Unlink patient/ }))
    await user.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Unlink' }))
    await waitFor(() => expect(calls.find((c) => c.method === 'DELETE' && c.path === '/implant/patients/p1/patient')).toBeDefined())
  })
})

describe('My preview', () => {
  const patientUser: User = { ...member, email: 'sana@example.test', display_name: 'Sana', implant_patient: true }
  const me = {
    linked: true,
    label: 'P-0001',
    clinic: 'Dr Rana',
    kept_until: '2027-01-24T09:00:00Z',
    photos: [{ id: 'ph-front', view: 'front', view_label: 'Front', url: '/api/implant-patient/files/ph-front' }],
    sessions: [{ id: 's1', created_at: '2026-10-24T09:30:00Z', by: 'clinic', options: [OPTIONS[0]], results: [{ id: 'r0', status: 'ready', url: '/api/implant-patient/files/r0', option: 0, view: 'front' }] }],
    approved: null,
    can_try: true,
  }

  it('asks for the clinic code when nothing is linked', async () => {
    const calls = mockApi((method, path) => {
      if (path === '/auth/me') return { ...patientUser, implant_patient: false }
      if (path === '/implant-patient/me') return { linked: false }
      if (path === '/implant-patient/link' && method === 'POST') return me
    })
    renderAt('/my-preview', [{ path: '/my-preview', element: <MyPreviewPage /> }])
    const user = userEvent.setup()
    const field = await screen.findByLabelText('Enter your clinic code')
    expect(screen.getByText('اَپْنے کِلِینِک کا کوڈ لِکھِیں')).toHaveAttribute('lang', 'ur')
    await user.type(field, 'k7m2qxpa')
    await user.click(screen.getByRole('button', { name: 'Link my record' }))
    await waitFor(() => expect(calls.find((c) => c.path === '/implant-patient/link')?.body).toEqual({ code: 'K7M2QXPA' }))
    expect(await screen.findByRole('heading', { name: 'Your photos' })).toBeInTheDocument()
  })

  it('shows her photos and previews without a way to save them, and tries a size', async () => {
    const calls = mockApi((method, path) => {
      if (path === '/auth/me') return patientUser
      if (path === '/implant-patient/me' && method === 'GET') return me
      if (path === '/implant-patient/me/try') return me
    })
    renderAt('/my-preview', [{ path: '/my-preview', element: <MyPreviewPage /> }])
    const user = userEvent.setup()
    const img = await screen.findByAltText('Front photo')
    expect(img).toHaveAttribute('draggable', 'false')
    expect(fireEvent.contextMenu(img)).toBe(false)
    expect(screen.queryByRole('link', { name: /download/i })).toBeNull()
    expect(screen.queryByLabelText(/upload/i)).toBeNull()
    const cc = screen.getByLabelText('Size (cc)')
    await user.clear(cc)
    await user.type(cc, '375')
    await user.selectOptions(screen.getByLabelText('Shape'), 'Teardrop')
    await user.click(screen.getByRole('button', { name: /Show me/ }))
    await waitFor(() =>
      expect(calls.find((c) => c.path === '/implant-patient/me/try')?.body).toEqual({
        option: { left_cc: 375, right_cc: 375, shape: 'teardrop', profile: 'moderate_plus', placement: 'dual_plane', fill: 'silicone', procedure: 'augmentation' },
      }),
    )
  })

  it('puts My preview in the rail only for a linked patient', async () => {
    mockApi((_m, p) => {
      if (p === '/system/status') return systemOk
      if (p === '/auth/me') return patientUser
    })
    renderAt('/', [], (qc) => qc.setQueryData(qk.jobs, []), <Rail />)
    const rail = screen.getByRole('navigation', { name: 'Main' })
    expect(await within(rail).findByRole('link', { name: 'My preview' })).toHaveAttribute('href', '/my-preview')
    expect(activeRail('/my-preview')).toBe('my_preview')
    expect(pageTitle('/my-preview')).toBe('My preview')
  })
})
