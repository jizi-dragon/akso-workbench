/**
 * 落盘层：会话文件可发现、可续读，半行不炸。
 */

import assert from 'node:assert/strict'
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'

import { CaptureStore } from '../src/store.ts'
import type { CaptureRecord } from '../src/records.ts'

function record(method: string, path: string, at: number): CaptureRecord {
  return {
    v: 1,
    id: `s:${at}`,
    sessionId: 's',
    at,
    method,
    url: `https://standard-val.aksoegmp.com${path}`,
    host: 'standard-val.aksoegmp.com',
    path,
    pathTemplate: path,
    query: {},
    requestHeaders: {},
  }
}

test('会话：追加、发现、读取、latest 与按 id 解析', () => {
  const root = mkdtempSync(join(tmpdir(), 'akw-capture-store-'))
  try {
    const store = new CaptureStore(root)
    store.append(record('GET', '/api/a', 1))
    store.append(record('POST', '/api/b', 2))

    assert.equal(store.size, 2)
    const sessions = CaptureStore.list(root)
    assert.equal(sessions.length, 1)
    assert.equal(sessions[0]?.file, store.file)
    assert.equal(sessions[0]?.records, 2)
    assert.equal(CaptureStore.latest(root), store.file)
    assert.equal(CaptureStore.resolve('latest', root), store.file)
    assert.equal(CaptureStore.resolve(store.sessionId, root), store.file)

    const rows = CaptureStore.read(store.file)
    assert.equal(rows.length, 2)
    assert.equal(rows[1]?.method, 'POST')
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('会话：坏行/半行跳过，好行照读', () => {
  const root = mkdtempSync(join(tmpdir(), 'akw-capture-bad-'))
  try {
    const store = new CaptureStore(root)
    store.append(record('GET', '/api/a', 1))
    writeFileSync(store.file, '{"half":', { flag: 'a' })
    const rows = CaptureStore.read(store.file)
    assert.equal(rows.length, 1)
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('会话：找不到时返回 null 而不是抛错', () => {
  const root = mkdtempSync(join(tmpdir(), 'akw-capture-empty-'))
  try {
    assert.equal(CaptureStore.latest(root), null)
    assert.equal(CaptureStore.resolve('latest', root), null)
    assert.equal(CaptureStore.resolve('nope', root), null)
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})
