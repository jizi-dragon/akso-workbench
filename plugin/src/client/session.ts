/** 当前 DSH 会话 id（由 client index 从 sidebarRight.mounted 同步到这里）。 */
let currentSessionId: string | null = null

export function setCurrentSessionId(value: unknown): void {
  if (typeof value === 'string' && value.trim() !== '') currentSessionId = value
  else if (value !== null && typeof value === 'object' && 'id' in value) {
    const id = (value as { id?: unknown }).id
    currentSessionId = typeof id === 'string' && id.trim() !== '' ? id : null
  } else currentSessionId = null
}

export function getCurrentSessionId(): string | null {
  return currentSessionId
}
