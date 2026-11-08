import { useRef, useState } from 'react'
import { Link, useLocation } from 'react-router'
import { Tooltip } from '@/components/ui/tooltip'
import { useConnections } from '@/hooks/useConnections'
import { useMe } from '@/hooks/useAuth'
import { useAllows } from '@/hooks/useUsage'
import { activeRail, ADMIN_ITEM, IMPLANT_ITEM, MODERATION_ITEM, PRINT_ITEM, RAIL_ITEMS, SETTINGS_ITEM, type MenuId, type RailId, type RailItem } from '@/lib/nav'
import { cn } from '@/lib/utils'
import { StatusDot } from './ConnectionStatus'
import { JobsTray } from './JobsTray'
import { MegaMenu } from './MegaMenu'
import { UserMenu } from './UserMenu'
import { useMegaMenu } from './useMegaMenu'
import { useMenus } from './useMenus'

const ITEM =
  'relative flex w-14 flex-col items-center gap-1 rounded-[6px] py-1.5 text-studio-muted transition-colors duration-150 hover:bg-studio-panel-hover hover:text-studio-text'
const ACTIVE = 'bg-studio-accent-soft text-studio-gold font-semibold'

function Indicator({ on }: { on: boolean }) {
  // gold bar on the rail's left edge; shape, not colour alone, marks the current section
  return on ? <span aria-hidden className="absolute -left-2 top-2 bottom-2 w-[3px] rounded-r bg-studio-gold bg-gold-leaf shadow-[0_0_10px_rgb(255_106_0/0.6)]" /> : null
}

function Face({ item }: { item: RailItem }) {
  return (
    <>
      <item.icon aria-hidden className="size-5" />
      {/* two-word labels ("Brand Kits") wrap onto a second line inside the 56 px item */}
      <span aria-hidden className="text-center text-small leading-[1.1]">
        {item.label}
      </span>
    </>
  )
}

export function Rail() {
  const { pathname } = useLocation()
  const active = activeRail(pathname)
  const root = useRef<HTMLElement>(null)
  const triggers = useRef(new Map<RailId, HTMLButtonElement>())
  const menu = useMegaMenu<RailId>(root)
  const conn = useConnections()
  const { data: me } = useMe()
  const menus = useMenus()
  const allows = useAllows()
  const staffItem = me?.admin ? ADMIN_ITEM : me?.staff === 'manager' ? MODERATION_ITEM : null
  // set on pointerdown so the click handler knows mouse vs touch vs keyboard
  const [pointer, setPointer] = useState<string | null>(null)

  const closeMenu = (returnFocus: boolean) => {
    const id = menu.open?.id
    menu.close()
    if (returnFocus && id) triggers.current.get(id)?.focus()
  }

  return (
    <nav
      ref={root}
      aria-label="Main"
      data-f6-region
      tabIndex={-1}
      className="relative z-40 flex w-[72px] shrink-0 flex-col items-center gap-1 overflow-y-auto overflow-x-hidden band border-r border-studio-border py-2 [scrollbar-color:rgb(255_184_0/0.35)_transparent] [scrollbar-width:thin] focus-visible:outline-none"
      onKeyDown={(e) => {
        if (e.key === 'Escape' && menu.open) closeMenu(true)
      }}
    >
      <Link to="/" aria-label="Jafat AI Studio, home" className="mb-2 rounded-[8px] shadow-[0_0_14px_rgb(255_106_0/0.25)]">
        <img src="/brand/jafat-icon-192.png" alt="" className="size-10 rounded-[8px]" />
      </Link>

      <ul className="flex flex-col items-center gap-1">
        {RAIL_ITEMS.filter((item) => allows(item.feature)).map((item) => {
          const on = active === item.id
          if (item.id === 'queue') {
            return (
              <li key={item.id} className="relative" onPointerEnter={() => menu.open && menu.hoverLeave()}>
                <Indicator on={on} />
                <JobsTray item={item} current={on} className={cn(ITEM, on && ACTIVE)} />
              </li>
            )
          }
          if (!item.menu) {
            return (
              <li key={item.id}>
                <Link
                  to={item.to}
                  aria-current={on ? 'page' : undefined}
                  aria-label={item.label}
                  className={cn(ITEM, on && ACTIVE)}
                  onPointerEnter={() => menu.open && menu.hoverLeave()}
                >
                  <Indicator on={on} />
                  <Face item={item} />
                </Link>
              </li>
            )
          }
          const isOpen = menu.open?.id === item.id
          const panelId = `megamenu-${item.id}`
          return (
            <li key={item.id}>
              <button
                ref={(el) => {
                  if (el) triggers.current.set(item.id, el)
                  else triggers.current.delete(item.id)
                }}
                id={`rail-${item.id}`}
                type="button"
                aria-expanded={isOpen}
                aria-controls={isOpen ? panelId : undefined}
                aria-current={on ? 'true' : undefined}
                aria-label={item.label}
                className={cn(ITEM, on && ACTIVE, isOpen && 'bg-studio-panel-hover text-studio-text')}
                onPointerDown={(e) => setPointer(e.pointerType)}
                onPointerEnter={(e) => e.pointerType === 'mouse' && menu.hoverEnter(item.id)}
                onPointerLeave={(e) => e.pointerType === 'mouse' && menu.hoverLeave()}
                onClick={(e) => {
                  // detail 0 = Enter/Space; touch toggles; a mouse click keeps a hover-opened menu open
                  const keyboard = e.detail === 0
                  if (keyboard || pointer !== 'mouse') menu.toggle(item.id, keyboard)
                  else menu.show(item.id, false)
                  setPointer(null)
                }}
                onKeyDown={(e) => {
                  if (e.key === 'ArrowDown' || e.key === 'ArrowRight') {
                    e.preventDefault()
                    menu.show(item.id, true)
                  }
                }}
              >
                <Indicator on={on} />
                <Face item={item} />
              </button>
              {isOpen && (
                <MegaMenu
                  // remount per open so keyboard focus lands on the first item again
                  key={`${item.id}-${menu.open!.keyboard}`}
                  id={panelId}
                  section={menus[item.id as MenuId] ?? item.menu}
                  anchorId={`rail-${item.id}`}
                  keyboard={menu.open!.keyboard}
                  onPick={() => menu.close()}
                  onClose={closeMenu}
                  onPointerEnter={menu.cancelClose}
                  onPointerLeave={(e) => e.pointerType === 'mouse' && menu.hoverLeave()}
                />
              )}
            </li>
          )
        })}
      </ul>

      <div className="mt-auto flex flex-col items-center gap-1">
        {(me?.admin || me?.implant) && (
          <Link
            to={IMPLANT_ITEM.to}
            aria-current={active === 'implant' ? 'page' : undefined}
            aria-label={IMPLANT_ITEM.label}
            className={cn(ITEM, active === 'implant' && ACTIVE)}
            onPointerEnter={() => menu.open && menu.hoverLeave()}
          >
            <Indicator on={active === 'implant'} />
            <Face item={IMPLANT_ITEM} />
          </Link>
        )}
        {allows('print') && (
          <Link
            to={PRINT_ITEM.to}
            aria-current={active === 'print' ? 'page' : undefined}
            aria-label={PRINT_ITEM.label}
            className={cn(ITEM, active === 'print' && ACTIVE)}
            onPointerEnter={() => menu.open && menu.hoverLeave()}
          >
            <Indicator on={active === 'print'} />
            <Face item={PRINT_ITEM} />
          </Link>
        )}
        {staffItem && (
          <Link
            to={staffItem.to}
            aria-current={active === 'admin' ? 'page' : undefined}
            aria-label={staffItem.label}
            className={cn(ITEM, active === 'admin' && ACTIVE)}
            onPointerEnter={() => menu.open && menu.hoverLeave()}
          >
            <Indicator on={active === 'admin'} />
            <Face item={staffItem} />
          </Link>
        )}
        <Tooltip
          side="right"
          content={
            <span className="block">
              ComfyUI: {conn.comfy === 'ok' ? 'connected' : conn.comfy === 'down' ? 'offline' : 'checking'} — {conn.comfyDetail}
              <br />
              Ollama: {conn.llm === 'ok' ? 'connected' : conn.llm === 'down' ? 'offline' : 'checking'} — {conn.llmDetail}
              {conn.mock && (
                <>
                  <br />
                  Mock renderer in use
                </>
              )}
            </span>
          }
        >
          <Link
            to={SETTINGS_ITEM.to}
            aria-current={active === 'settings' ? 'page' : undefined}
            aria-label={`Settings. ${conn.summary}`}
            className={cn(ITEM, active === 'settings' && ACTIVE)}
            onPointerEnter={() => menu.open && menu.hoverLeave()}
          >
            <Indicator on={active === 'settings'} />
            <span className="relative">
              <SETTINGS_ITEM.icon aria-hidden className="size-5" />
              <StatusDot state={conn.overall} className="absolute -right-1 -top-0.5 size-2.5 ring-2 ring-studio-panel" />
            </span>
            <span aria-hidden className="text-small leading-none">
              Settings
            </span>
          </Link>
        </Tooltip>
        <UserMenu rail />
      </div>
    </nav>
  )
}
