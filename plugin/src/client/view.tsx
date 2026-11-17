/**
 * 查看相关的共用小件：折叠态持久化、滚动位置记忆、桌面断点。
 *
 * 这里只解决「看的时候别丢位置」这一类问题，不引入新的视觉语言：
 * 展开态存在 localStorage，滚动位置存在 sessionStorage，读写失败一律静默降级
 * （隐私模式 / 配额满 / 嵌入的 webview 都可能禁掉存储，页面不能因此崩）。
 */
import { useEffect, useRef, useState, type ReactNode } from 'react'

const KEY_PREFIX = 'akw.view.'

function readJson<T>(key: string, fallback: T): T {
  try {
    const raw = window.localStorage.getItem(KEY_PREFIX + key)
    if (raw === null) return fallback
    return JSON.parse(raw) as T
  } catch {
    return fallback
  }
}

function writeJson(key: string, value: unknown): void {
  try {
    window.localStorage.setItem(KEY_PREFIX + key, JSON.stringify(value))
  } catch {
    // 存不下就不存：查看状态丢了不影响功能。
  }
}

/** localStorage 里的一个状态；同 key 的多个组件刷新后保持一致。 */
export function usePersisted<T>(key: string, initial: T): [T, (value: T) => void] {
  const [value, setValue] = useState<T>(() => readJson(key, initial))
  const update = (next: T): void => {
    setValue(next)
    writeJson(key, next)
  }
  return [value, update]
}

export interface FoldSet {
  /** `fallback` 用于「第一次打开时希望默认展开」的场景（例如单对象分析）。 */
  isOpen: (id: string, fallback?: boolean) => boolean
  setOpen: (id: string, value: boolean) => void
  setAll: (ids: string[], value: boolean) => void
}

/**
 * 一组折叠项的展开态。key 只存布尔值，按 id 记；
 * 用 scope 分开（账号卡、对象卡），互不干扰。
 */
export function useFoldSet(scope: string): FoldSet {
  const [map, setMap] = useState<Record<string, boolean>>(() => readJson(`folds.${scope}`, {}))
  const commit = (next: Record<string, boolean>): void => {
    setMap(next)
    writeJson(`folds.${scope}`, next)
  }
  return {
    isOpen: (id, fallback = false) => (Object.prototype.hasOwnProperty.call(map, id) ? map[id] === true : fallback),
    setOpen: (id, value) => commit({ ...map, [id]: value }),
    setAll: (ids, value) => {
      const next = { ...map }
      for (const id of ids) next[id] = value
      commit(next)
    },
  }
}

/** 页面内滚动容器：切模块再回来时回到上次的位置。 */
export function ScrollBody({ id, children }: { id: string; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const element = ref.current
    if (element === null) return
    const key = `akw.scroll.${id}`
    const saved = Number(window.sessionStorage.getItem(key) ?? '')
    if (Number.isFinite(saved) && saved > 0) element.scrollTop = saved
    const onScroll = (): void => {
      try {
        window.sessionStorage.setItem(key, String(element.scrollTop))
      } catch {
        // 同 localStorage：存不下就退化成「回到顶部」。
      }
    }
    element.addEventListener('scroll', onScroll, { passive: true })
    return () => {
      element.removeEventListener('scroll', onScroll)
      onScroll()
    }
  }, [id])
  return (
    <div className="akw-body" ref={ref}>
      {children}
    </div>
  )
}

/** 桌面断点：宽屏把「列表 + 详情」并排放，窄屏仍用弹窗。 */
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() => (typeof window === 'undefined' ? false : window.matchMedia(query).matches))
  useEffect(() => {
    const media = window.matchMedia(query)
    const onChange = (): void => setMatches(media.matches)
    setMatches(media.matches)
    media.addEventListener('change', onChange)
    return () => media.removeEventListener('change', onChange)
  }, [query])
  return matches
}
