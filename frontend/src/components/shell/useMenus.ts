import { useCatalog } from '@/hooks/useModels'
import { useAllows } from '@/hooks/useUsage'
import { allowedMenus, menusFrom } from '@/lib/nav'

/** Mega-menu sections with their Models column filled from the live catalog (already only what the person's
 * plan has), and without the studios it doesn't. */
export const useMenus = () => allowedMenus(menusFrom(useCatalog()), useAllows())
