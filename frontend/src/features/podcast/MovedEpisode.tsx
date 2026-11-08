import { Navigate, useParams } from 'react-router'

/** An episode's old /audio address, or its old separate AI video page: the same episode in Podcast Studio. */
export function MovedEpisode({ video = false }: { video?: boolean }) {
  const { id = '' } = useParams()
  return <Navigate to={`/podcast/${id}${video ? '?step=cast' : ''}`} replace />
}
