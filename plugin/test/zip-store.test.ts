import assert from 'node:assert/strict'
import test from 'node:test'

import { zipStore } from '../src/client/zip-store.ts'

const encoder = new TextEncoder()

test('zipStore：生成带上层文件夹的 zip，且拒绝路径穿越', () => {
  const date = new Date(2026, 10, 13, 16, 1, 41)
  const bytes = zipStore(
    [
      { name: 'obj-analysis-20261113-160141/', data: new Uint8Array(0) },
      { name: 'obj-analysis-20261113-160141/obj-analysis-20261113-160141.md', data: encoder.encode('# hi') },
    ],
    date,
  )

  assert.equal(new TextDecoder().decode(bytes.slice(0, 4)), 'PK\u0003\u0004')
  const text = new TextDecoder().decode(bytes)
  assert.match(text, /obj-analysis-20261113-160141\//)
  assert.match(text, /obj-analysis-20261113-160141\.md/)
  assert.equal(new TextDecoder().decode(bytes.slice(-22, -18)), 'PK\u0005\u0006')

  assert.throws(() => zipStore([{ name: '../evil.txt', data: new Uint8Array(0) }]), /路径不安全/)
  assert.throws(() => zipStore([{ name: '/abs.txt', data: new Uint8Array(0) }]), /路径不安全/)
})
