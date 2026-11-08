import { createBrowserRouter, Navigate, type RouteObject } from 'react-router'
import { LoginPage } from '@/features/auth/LoginPage'
import { SignUpPage } from '@/features/auth/SignUpPage'
import { ForgotPasswordPage, ResetPasswordPage, VerifyEmailPage } from '@/features/auth/PasswordPages'
import { RequireAuth } from '@/features/auth/RequireAuth'
import { AppShell } from '@/components/shell/AppShell'
import { ProjectsPage } from '@/features/projects/ProjectsPage'
import { NotFound } from '@/features/stages/NotFound'
import { MovedEpisode } from '@/features/podcast/MovedEpisode'

const page = (path: string, load: () => Promise<React.ReactElement>): RouteObject => ({
  path,
  lazy: async () => ({ element: await load() }),
})

// Everything behind the rail. Exported so tests can mount the real route table.
export const shellRoutes: RouteObject[] = [
  { index: true, lazy: async () => ({ Component: (await import('@/features/home/HomePage')).HomePage }) },

  // Video
  page('video/quick', async () => {
    const { QuickCreatePage } = await import('@/features/quick/QuickCreatePage')
    return <QuickCreatePage />
  }),
  page('video/create', async () => {
    const { VideoCreatePage } = await import('@/features/video/VideoCreatePage')
    return <VideoCreatePage />
  }),
  page('video/img2vid', async () => {
    const { Img2VidPage } = await import('@/features/video/Img2VidPage')
    return <Img2VidPage />
  }),
  { path: 'video/projects', element: <ProjectsPage /> },
  page('video/poetry', async () => {
    const { PoetryListPage } = await import('@/features/video/poetry/PoetryListPage')
    return <PoetryListPage />
  }),
  page('video/presenter', async () => {
    const { PresenterListPage } = await import('@/features/video/presenter/PresenterListPage')
    return <PresenterListPage />
  }),
  page('video/presenter/:id', async () => {
    const { PresenterPage } = await import('@/features/video/presenter/PresenterPage')
    return <PresenterPage />
  }),
  page('video/poetry/:id', async () => {
    const { PoetryShortPage } = await import('@/features/video/poetry/PoetryShortPage')
    return <PoetryShortPage />
  }),
  page('video/prompt-templates', async () => {
    const { TemplatesPage } = await import('@/features/templates/TemplatesPage')
    return <TemplatesPage type="video" />
  }),
  // the old name; bookmarks keep working
  { path: 'video/templates', element: <Navigate to="/video/prompt-templates" replace /> },
  page('video/upscale', async () => {
    const { UpscalePickPage } = await import('@/features/upscale/UpscalePickPage')
    return <UpscalePickPage kind="video" />
  }),
  page('video/library', async () => {
    const { LibraryPage } = await import('@/features/library/LibraryPage')
    return <LibraryPage kind="video" />
  }),

  // Image
  page('image/generate', async () => {
    const { ImageGeneratePage } = await import('@/features/image/ImageGeneratePage')
    return <ImageGeneratePage />
  }),
  page('image/edit', async () => {
    const { ImageEditPage } = await import('@/features/image/ImageEditPage')
    return <ImageEditPage />
  }),
  page('image/brand', async () => {
    const { BrandTemplatePage } = await import('@/features/image/BrandTemplatePage')
    return <BrandTemplatePage />
  }),
  page('image/ads', async () => {
    const { AdStudioPage } = await import('@/features/image/ads/AdStudioPage')
    return <AdStudioPage />
  }),
  page('image/ads/:id', async () => {
    const { AdCampaignPage } = await import('@/features/image/ads/AdCampaignPage')
    return <AdCampaignPage />
  }),
  page('image/img2img', async () => {
    const { Img2ImgPage } = await import('@/features/image/Img2ImgPage')
    return <Img2ImgPage />
  }),
  page('image/control', async () => {
    const { ControlPage } = await import('@/features/image/ControlPage')
    return <ControlPage />
  }),
  page('image/references', async () => {
    const { ImageReferencesPage } = await import('@/features/image/ImageReferencesPage')
    return <ImageReferencesPage />
  }),
  page('image/prompt-templates', async () => {
    const { TemplatesPage } = await import('@/features/templates/TemplatesPage')
    return <TemplatesPage type="image" />
  }),
  // the old name; bookmarks keep working
  { path: 'image/templates', element: <Navigate to="/image/prompt-templates" replace /> },
  page('image/upscale', async () => {
    const { UpscalePickPage } = await import('@/features/upscale/UpscalePickPage')
    return <UpscalePickPage kind="image" />
  }),
  page('image/studio', async () => {
    const { PhotoStudioPickPage } = await import('@/features/photo/PhotoStudioPickPage')
    return <PhotoStudioPickPage />
  }),
  page('image/studio/:generationId', async () => {
    const { PhotoStudioPage } = await import('@/features/photo/PhotoStudioPage')
    return <PhotoStudioPage />
  }),
  page('photos', async () => {
    const { PhotosPage } = await import('@/features/photos/PhotosPage')
    return <PhotosPage />
  }),
  page('image/library', async () => {
    const { LibraryPage } = await import('@/features/library/LibraryPage')
    return <LibraryPage kind="image" />
  }),
  // Audio: one workspace frame (sidebar, status strip, transport) around every page (contract v14, v15, v16)
  {
    path: 'audio',
    lazy: async () => ({ Component: (await import('@/features/audio/shell/AudioWorkspace')).AudioWorkspace }),
    children: [
      { index: true, element: <Navigate to="/audio/song" replace /> },
      page('song', async () => {
        const { SongPage } = await import('@/features/audio/SongPage')
        return <SongPage />
      }),
      page('music', async () => {
        const { MusicPage } = await import('@/features/audio/MusicPage')
        return <MusicPage />
      }),
      page('sfx', async () => {
        const { SfxPage } = await import('@/features/audio/SfxPage')
        return <SfxPage />
      }),
      page('speak', async () => {
        const { SpeakPage } = await import('@/features/audio/voice/SpeakPage')
        return <SpeakPage />
      }),
      page('voices', async () => {
        const { VoicesPage } = await import('@/features/audio/voice/VoicesPage')
        return <VoicesPage />
      }),
      page('gallery', async () => {
        const { GalleryPage } = await import('@/features/audio/voice/GalleryPage')
        return <GalleryPage />
      }),
      page('design', async () => {
        const { DesignPage } = await import('@/features/audio/voice/DesignPage')
        return <DesignPage />
      }),
      page('dialogue', async () => {
        const { DialogueListPage } = await import('@/features/audio/dialogue/DialogueListPage')
        return <DialogueListPage />
      }),
      page('dialogue/:id', async () => {
        const { DialogueEditorPage } = await import('@/features/audio/dialogue/DialogueEditorPage')
        return <DialogueEditorPage />
      }),
      // Podcast Studio moved out to /podcast (Phase 4); old bookmarks follow it
      { path: 'podcast', element: <Navigate to="/podcast" replace /> },
      { path: 'podcast/:id', element: <MovedEpisode /> },
      { path: 'podcast/:id/video', element: <MovedEpisode video /> },
      page('edit', async () => {
        const { EditPickPage } = await import('@/features/audio/editor/EditPickPage')
        return <EditPickPage />
      }),
      page('edit/:id', async () => {
        const { EditorPage } = await import('@/features/audio/editor/EditorPage')
        return <EditorPage />
      }),
      page('score', async () => {
        const { ScorePage } = await import('@/features/audio/score/ScorePage')
        return <ScorePage />
      }),
      // contract v18
      page('transcribe', async () => {
        const { TranscribePage } = await import('@/features/audio/transcribe/TranscribePage')
        return <TranscribePage />
      }),
      // contract v21
      page('stems', async () => {
        const { StemsPage } = await import('@/features/audio/stems/StemsPage')
        return <StemsPage />
      }),
      page('dub', async () => {
        const { DubListPage } = await import('@/features/audio/dub/DubListPage')
        return <DubListPage />
      }),
      page('dub/:id', async () => {
        const { DubWorkspacePage } = await import('@/features/audio/dub/DubWorkspacePage')
        return <DubWorkspacePage />
      }),
      page('prompt-templates', async () => {
        const { TemplatesPage } = await import('@/features/templates/TemplatesPage')
        return <TemplatesPage type="audio" />
      }),
      page('library', async () => {
        const { LibraryPage } = await import('@/features/library/LibraryPage')
        return <LibraryPage kind="audio" />
      }),
    ],
  },

  // Phase 4: Podcast Studio, audio or video episodes; the frame keeps the audio transport for the voice steps
  {
    path: 'podcast',
    lazy: async () => ({ Component: (await import('@/features/podcast/PodcastFrame')).PodcastFrame }),
    children: [
      {
        index: true,
        lazy: async () => ({ Component: (await import('@/features/podcast/PodcastListPage')).PodcastListPage }),
      },
      page(':id', async () => {
        const { EpisodePage } = await import('@/features/podcast/EpisodePage')
        return <EpisodePage />
      }),
      { path: ':id/video', element: <MovedEpisode video /> },
    ],
  },

  // contract-v25: Character Studio
  page('characters', async () => {
    const { CharacterStudioPage } = await import('@/features/characters/CharacterStudioPage')
    return <CharacterStudioPage />
  }),
  page('characters/:characterId', async () => {
    const { CharacterPage } = await import('@/features/characters/CharacterPage')
    return <CharacterPage />
  }),

  // contract v40: 3D Studio
  page('3d', async () => {
    const { Studio3DPage } = await import('@/features/model3d/Studio3DPage')
    return <Studio3DPage />
  }),
  page('3d/library', async () => {
    const { LibraryPage } = await import('@/features/library/LibraryPage')
    return <LibraryPage kind="model" />
  }),

  { path: 'image', element: <Navigate to="/image/generate" replace /> },
  { path: 'video', element: <Navigate to="/video/quick" replace /> },

  page('assets', async () => {
    const { AssetsPage } = await import('@/features/assets/AssetsPage')
    return <AssetsPage />
  }),
  page('brand-kits', async () => {
    const { BrandKitsPage } = await import('@/features/brand/BrandKitsPage')
    return <BrandKitsPage />
  }),
  page('brand-kits/:kitId', async () => {
    const { BrandKitEditorPage } = await import('@/features/brand/BrandKitEditorPage')
    return <BrandKitEditorPage />
  }),
  page('queue', async () => {
    const { QueuePage } = await import('@/features/queue/QueuePage')
    return <QueuePage />
  }),
  page('settings', async () => {
    const { SettingsPage } = await import('@/features/settings/SettingsPage')
    return <SettingsPage />
  }),
  page('settings/account', async () => {
    const { AccountPage } = await import('@/features/settings/AccountPage')
    return <AccountPage />
  }),
  // contract v38/v39: admin only; the section sends anyone else home and the server answers them 404
  {
    path: 'admin',
    lazy: async () => ({ Component: (await import('@/features/admin/shared')).AdminLayout }),
    children: [
      { index: true, lazy: async () => ({ Component: (await import('@/features/admin/OverviewPage')).OverviewPage }) },
      page('users', async () => {
        const { UsersPage } = await import('@/features/admin/UsersPage')
        return <UsersPage />
      }),
      page('packages', async () => {
        const { PackagesPage } = await import('@/features/admin/PackagesPage')
        return <PackagesPage />
      }),
      page('subscriptions', async () => {
        const { SubscriptionsPage } = await import('@/features/admin/SubscriptionsPage')
        return <SubscriptionsPage />
      }),
      page('payments', async () => {
        const { PaymentsPage } = await import('@/features/admin/PaymentsPage')
        return <PaymentsPage />
      }),
      page('usage', async () => {
        const { UsagePage } = await import('@/features/admin/UsagePage')
        return <UsagePage />
      }),
      page('audit', async () => {
        const { AuditPage } = await import('@/features/admin/AuditPage')
        return <AuditPage />
      }),
      page('bulk', async () => {
        const { BulkPage } = await import('@/features/admin/bulk/BulkPage')
        return <BulkPage />
      }),
      page('bulk/:id', async () => {
        const { BulkBatchPage } = await import('@/features/admin/bulk/BulkBatchPage')
        return <BulkBatchPage />
      }),
      // contract v41: Print Studio, inside the admin section so its guard covers it
      page('print', async () => {
        const { PrintStudioPage } = await import('@/features/print/PrintStudioPage')
        return <PrintStudioPage />
      }),
      page('print/:id', async () => {
        const { PrintJobPage } = await import('@/features/print/PrintJobPage')
        return <PrintJobPage />
      }),
    ],
  },
  // M22: Implant Preview, for admins and granted surgeons only
  {
    path: 'clinic/implant',
    lazy: async () => ({ Component: (await import('@/features/implant/ImplantLayout')).ImplantLayout }),
    children: [
      { index: true, lazy: async () => ({ Component: (await import('@/features/implant/ImplantPatientsPage')).ImplantPatientsPage }) },
      page(':id', async () => {
        const { ImplantPatientPage } = await import('@/features/implant/ImplantPatientPage')
        return <ImplantPatientPage />
      }),
    ],
  },
  // contract v27: admin only while PUBLIC_API is off (the page sends anyone else back to Settings)
  page('settings/api', async () => {
    const { ApiTokensPage } = await import('@/features/settings/ApiTokensPage')
    return <ApiTokensPage />
  }),

  // Older addresses people have bookmarked
  { path: 'projects', element: <Navigate to="/video/projects" replace /> },
  { path: 'create', element: <Navigate to="/video/quick" replace /> },

  { path: 'projects/:projectId', element: <Navigate to="script" replace /> },
  {
    // not :projectId, so the top bar stays out of studio mode on the progress screen
    path: 'quick/:quickId',
    lazy: async () => ({ Component: (await import('@/features/quick/QuickProgressPage')).QuickProgressPage }),
  },
  {
    path: 'projects/:projectId/:stage',
    lazy: async () => ({ Component: (await import('@/features/workspace/WorkspacePage')).WorkspacePage }),
  },
]

const routes: RouteObject[] = [
  { path: '/login', element: <LoginPage /> },
  // contract v38: signed-out pages; the server's emails link to the last three
  { path: '/signup', element: <SignUpPage /> },
  { path: '/forgot-password', element: <ForgotPasswordPage /> },
  { path: '/reset-password', element: <ResetPasswordPage /> },
  { path: '/verify-email', element: <VerifyEmailPage /> },
  // the public site; '/' itself is the landing for visitors, see RequireAuth
  { path: '/studio', lazy: async () => ({ Component: (await import('@/features/landing/StudioPage')).StudioPage }) },
  { path: '/plans', lazy: async () => ({ Component: (await import('@/features/landing/PlansPage')).PlansPage }) },
  { path: '/about', lazy: async () => ({ Component: (await import('@/features/landing/AboutPage')).AboutPage }) },
  {
    element: <RequireAuth />,
    children: [{ element: <AppShell />, children: shellRoutes }],
  },
]

// Component gallery for visual QA; never shipped in production bundles.
if (import.meta.env.DEV) {
  routes.push({
    path: '/dev/ui',
    lazy: async () => ({ Component: (await import('@/features/dev/DevUiPage')).DevUiPage }),
  })
}

routes.push({ path: '*', element: <NotFound /> })

export const router = createBrowserRouter(routes)
