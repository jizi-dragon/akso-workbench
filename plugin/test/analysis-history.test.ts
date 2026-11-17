import assert from 'node:assert/strict'
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'

import type { AnalysisResult } from '../src/analysis.ts'
import {
  analysisHistoryDir,
  listAnalysisHistory,
  readAnalysisHistory,
  saveAnalysisHistory,
  saveFailedAnalysisHistory,
} from '../src/analysis-history.ts'

function sampleResult(query: string): AnalysisResult {
  return {
    source: { baseUrl: 'https://example.test', account: 'qa' },
    object: { id: 'o-1', code: query, name: '示例对象', lifecycleId: 'l-1', enableLifeCycle: true },
    lifecycle: { id: 'l-1', name: '生命周期' },
    statuses: [],
    fields: [],
    workflows: [],
    totals: { statuses: 2, enabledStatuses: 1, userActions: 3, enterActions: 1, fields: 7 },
    enterCondition: { count: 0, configured: false, readable: true },
    warnings: [],
  } as unknown as AnalysisResult
}

function withHome(run: (home: string) => void): void {
  const home = mkdtempSync(join(tmpdir(), 'akw-history-'))
  try {
    run(home)
  } finally {
    rmSync(home, { recursive: true, force: true })
  }
}

test('分析历史：成功记录能列出、能读回结果', () => {
  withHome((home) => {
    const saved = saveAnalysisHistory(home, 'demo__c', sampleResult('demo__c'))
    assert.equal(saved.status, 'ok')
    assert.equal(saved.objectName, '示例对象')

    const items = listAnalysisHistory(home)
    assert.equal(items.length, 1)
    assert.equal(items[0]?.status, 'ok')
    assert.equal(items[0]?.id, saved.id)
    assert.equal(items[0]?.error, undefined)

    const detail = readAnalysisHistory(home, saved.id)
    assert.equal(detail?.status, 'ok')
    assert.equal(detail?.query, 'demo__c')
    assert.equal(detail?.result?.object.code, 'demo__c')
  })
})

test('分析历史：失败记录带错误也能列出、能读回原因', () => {
  withHome((home) => {
    const saved = saveFailedAnalysisHistory(home, 'missing__c', '对象 missing__c 没有返回详情', {
      baseUrl: 'https://example.test',
      account: 'qa',
    })
    assert.equal(saved.status, 'failed')

    const items = listAnalysisHistory(home)
    assert.equal(items.length, 1)
    assert.equal(items[0]?.status, 'failed')
    assert.equal(items[0]?.objectCode, '')
    assert.equal(items[0]?.error, '对象 missing__c 没有返回详情')
    assert.equal(items[0]?.baseUrl, 'https://example.test')

    const detail = readAnalysisHistory(home, saved.id)
    assert.equal(detail?.status, 'failed')
    assert.equal(detail?.query, 'missing__c')
    assert.equal(detail?.error, '对象 missing__c 没有返回详情')
    assert.equal(detail?.result, undefined)
  })
})

test('分析历史：失败与成功混排时按时间倒序，最新在前', async () => {
  const home = mkdtempSync(join(tmpdir(), 'akw-history-'))
  try {
    saveAnalysisHistory(home, 'first__c', sampleResult('first__c'))
    await new Promise((resolve) => setTimeout(resolve, 5))
    saveFailedAnalysisHistory(home, 'second__c', 'boom')
    const items = listAnalysisHistory(home)
    assert.equal(items.length, 2)
    assert.equal(items[0]?.query, 'second__c')
    assert.equal(items[0]?.status, 'failed')
    assert.equal(items[1]?.query, 'first__c')
  } finally {
    rmSync(home, { recursive: true, force: true })
  }
})

test('分析历史：旧记录没有 status 但有 result，按成功兼容', () => {
  withHome((home) => {
    const dir = analysisHistoryDir(home)
    saveAnalysisHistory(home, 'legacy__c', sampleResult('legacy__c'))
    assert.ok(dir.length > 0)
    const [item] = listAnalysisHistory(home)
    assert.ok(item !== undefined)
    const file = join(dir, `${item.id}.json`)
    const stored = JSON.parse(readFileSync(file, 'utf8')) as Record<string, unknown>
    delete stored.status
    writeFileSync(file, JSON.stringify(stored), 'utf8')

    const items = listAnalysisHistory(home)
    assert.equal(items.length, 1)
    assert.equal(items[0]?.status, 'ok')
    assert.equal(readAnalysisHistory(home, item.id)?.status, 'ok')
  })
})

test('分析历史：坏文件跳过，不拖垮整个列表', () => {
  withHome((home) => {
    saveAnalysisHistory(home, 'ok__c', sampleResult('ok__c'))
    writeFileSync(join(analysisHistoryDir(home), 'broken.json'), '{not json', 'utf8')
    const items = listAnalysisHistory(home)
    assert.equal(items.length, 1)
    assert.equal(items[0]?.query, 'ok__c')
  })
})

test('分析历史：id 只接受生成器写出的字符，挡目录穿越', () => {
  withHome((home) => {
    saveAnalysisHistory(home, 'demo__c', sampleResult('demo__c'))
    assert.equal(readAnalysisHistory(home, '../prefs'), null)
    assert.equal(readAnalysisHistory(home, 'not-there'), null)
  })
})
